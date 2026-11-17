"""Memória curta por sessão e contexto; feedback separado dos dados de ML."""
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import os
import re
import unicodedata
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException

from .acesso_web import conexao_web
from .evidencias_genai import contextualizar, preparar, contexto_com_evidencias, conferir_referencias

TTL = 30 * 60
MAX_CONVERSAS = 100
MAX_PERGUNTAS = 20
PARES_MEMORIA = 6


@dataclass
class Conversa:
    dono: str
    pedido: dict | None
    acesso: float = field(default_factory=monotonic)
    historico: list = field(default_factory=list)
    requisicoes: dict = field(default_factory=dict)
    trava: Lock = field(default_factory=Lock)


_conversas = OrderedDict()
_trava = Lock()


def dono_sessao(usuario):
    # CSRF é diferente em cada login. Não guardamos o token em texto claro.
    return sha256(f'{usuario["id"]}:{usuario["perfil"]}:{usuario["csrf"]}'.encode()).hexdigest()


def criar(usuario, pedido=None):
    with _trava:
        agora = monotonic()
        for chave, conversa in list(_conversas.items()):
            if agora - conversa.acesso > TTL and not conversa.trava.locked():
                del _conversas[chave]
        if len(_conversas) >= MAX_CONVERSAS:
            raise HTTPException(503, "O assistente está ocupado. Tente novamente em instantes.")
        identificador = str(uuid4())
        _conversas[identificador] = Conversa(dono_sessao(usuario), pedido)
        return identificador


def obter(identificador, usuario):
    with _trava:
        conversa = _conversas.get(str(identificador))
        if conversa is None or conversa.dono != dono_sessao(usuario):
            raise HTTPException(404, "Conversa indisponível. Inicie uma nova conversa.")
        if monotonic() - conversa.acesso > TTL and not conversa.trava.locked():
            del _conversas[str(identificador)]
            raise HTTPException(410, "A conversa expirou. Inicie uma nova conversa.")
        conversa.acesso = monotonic()
        return conversa


def apagar(identificador, usuario):
    obter(identificador, usuario)
    with _trava:
        _conversas.pop(str(identificador), None)


def normalizar(valor):
    return "".join(c for c in unicodedata.normalize("NFD", valor.casefold())
                   if unicodedata.category(c) != "Mn")


def somente_encaminhamento(texto):
    limpo = normalizar(re.sub(r"\[E\d+(?:[,;]\s*E\d+)*\]", "", texto))
    caminho = any(c in limpo for c in ("meus pedidos", "conversar sobre pedido"))
    fato_ou_limite = re.search(
        r"\d|\b(nao|nunca|sem|somente|apenas|prazo|data|status|entreg\w*|preparac\w*|"
        r"score|frete|responsavel|cancel\w*|extern\w*|transportadora|valor|dias?|probabilidade)\b", limpo)
    return bool(caminho and not fato_ou_limite)


def corrigir_reencaminhamento_sem_prazo(texto, pergunta, contexto):
    """Corrige o caso estrito de voltar à própria tela para procurar prazo ausente."""
    pedido = contexto.get("pedido") or {}
    if contexto.get("fonte_dados") != "loja" or not all(
        k in pedido and pedido[k] is None for k in ("prazo_prometido", "prazo_original")
    ):
        return texto, False
    duvida = normalizar(pergunta)
    if (not re.search(r"\b(data|prazo|chega|chegar|entrega)\b", duvida) or
        re.search(r"\b(outro|outros|trocar|selecionar|escolher|editar|alterar|atualizar|mudar|preencher)\b", duvida)):
        return texto, False
    frases = separar_frases(texto)
    restantes = [f.group() for f in frases if not somente_encaminhamento(f.group())]
    if len(restantes) == len(frases):
        return texto, False
    restante = "".join(restantes).strip()
    if restante:
        return restante, True
    return ("Já estamos conversando sobre este pedido. Os prazos prometido atual e original "
            "não foram informados nesta consulta [E6, E7]."), True


def separar_frases(texto):
    # Ponto decimal não encerra frase; citação após ponto acompanha a frase anterior.
    citacao = r"\[E\d+(?:[,;]\s*E\d+)*\]"
    return list(re.finditer(r".+?(?:[.!?](?:[ \t]*" + citacao + r")*(?=\s|$)|$)", texto, re.S))


def reduzir_orientacoes_repetidas(texto, historico, pergunta):
    """Remove somente encaminhamentos repetidos; preserva fatos e pedidos de ajuda."""
    # Na dúvida, manter a orientação: o usuário pode estar pedindo os passos de novo.
    if re.search(r"\b(onde|como|caminho|passos?|botao|cliq\w*|acess\w*|abrir|rep\w*|novamente|explic\w*|entend\w*|lembr\w*|esquec\w*|ajud\w*|fazer|faco)\b|de novo|o que (e|significa|faz)\b",
                 normalizar(pergunta)):
        return texto, 0
    anteriores = normalizar(" ".join(m["texto"] for m in historico if m["papel"] == "model"))
    caminhos = ("meus pedidos", "conversar sobre pedido")
    if not all(c in anteriores for c in caminhos):
        return texto, 0

    frases = separar_frases(texto)
    preservadas = []
    removidas = 0
    for frase in frases:
        trecho = frase.group()
        if somente_encaminhamento(trecho):
            removidas += 1
        else:
            preservadas.append(trecho)
    restante = "".join(preservadas).strip()
    # Não gerar resposta vazia nem fabricar texto substituto.
    if removidas and restante:
        return restante, removidas
    return texto, 0


def conversar(identificador, usuario, requisicao, pergunta, contexto, gerar):
    conversa = obter(identificador, usuario)
    if not conversa.trava.acquire(blocking=False):
        raise HTTPException(409, "Aguarde a resposta anterior antes de enviar outra pergunta.")
    try:
        chave = str(requisicao)
        anterior = conversa.requisicoes.get(chave)
        if anterior and anterior["pergunta"] != pergunta:
            raise HTTPException(409, "Esta tentativa pertence a outra pergunta.")
        if anterior and anterior.get("resultado"):
            return anterior["resultado"]
        if anterior is None:
            if len(conversa.requisicoes) >= MAX_PERGUNTAS:
                raise HTTPException(409, "Limite desta conversa atingido. Inicie uma nova conversa.")
            conversa.requisicoes[chave] = {"pergunta": pergunta}
        contexto = contextualizar(contexto, usuario["perfil"], pergunta=pergunta)
        evidencias = preparar(contexto)
        resultado = gerar(pergunta, contexto_com_evidencias(contexto, evidencias), historico=list(conversa.historico))
        # Texto truncado não entra na memória nem recebe avaliação.
        if resultado.get("disponivel") and resultado.get("motivo_termino") not in (None, "STOP"):
            resultado = {"disponivel": False, "resposta": "A IA não concluiu a resposta. Tente novamente."}
        resultado = {**resultado, "conversa_id": str(identificador)}
        if resultado.get("disponivel"):
            citadas, desconhecidas = conferir_referencias(resultado["resposta"], evidencias)
            if desconhecidas:
                return {"disponivel": False, "conversa_id": str(identificador),
                        "resposta": "A IA citou uma referência que não está nesta consulta. Tente novamente."}
            if usuario["perfil"] == "cliente":
                texto, removidas = reduzir_orientacoes_repetidas(resultado["resposta"], conversa.historico, pergunta)
                texto, corrigida = corrigir_reencaminhamento_sem_prazo(texto, pergunta, contexto)
                resultado = {**resultado, "resposta": texto, "orientacoes_repetidas_removidas": removidas,
                             "orientacao_corrigida_com_contexto": corrigida}
                citadas, _ = conferir_referencias(texto, evidencias)
            # O provedor não escolhe a fonte, o horário nem os valores exibidos.
            resultado["evidencias"] = {**evidencias, "citadas": citadas}
            resultado["resposta_id"] = str(uuid4())
            conversa.requisicoes[chave].update(resultado=resultado, modelo=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))
            conversa.historico.extend([
                {"papel": "user", "texto": pergunta},
                {"papel": "model", "texto": resultado["resposta"]},
            ])
            conversa.historico = conversa.historico[-PARES_MEMORIA * 2:]
        return resultado
    finally:
        conversa.acesso = monotonic()
        conversa.trava.release()


def avaliar(usuario, conversa_id, resposta_id, avaliacao, categoria):
    conversa = obter(conversa_id, usuario)
    with conversa.trava:
        registro = next((r.copy() for r in conversa.requisicoes.values()
                         if r.get("resultado", {}).get("resposta_id") == str(resposta_id)), None)
    if registro is None:
        raise HTTPException(404, "Resposta indisponível para avaliação.")
    if avaliacao == 1:
        categoria = None
    elif not categoria:
        raise HTTPException(422, "Selecione o motivo da avaliação.")
    with conexao_web() as conn:
        conn.execute("""
            INSERT INTO assistente_feedback
                (resposta_id, usuario_id, avaliacao, categoria, pergunta, resposta, contexto, modelo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (resposta_id) DO UPDATE SET
                avaliacao=EXCLUDED.avaliacao, categoria=EXCLUDED.categoria, atualizado_em=now()
            WHERE assistente_feedback.usuario_id=EXCLUDED.usuario_id
        """, (str(resposta_id), usuario["id"], avaliacao, categoria, registro["pergunta"],
              registro["resultado"]["resposta"], (conversa.pedido or {}).get("tipo", "geral"), registro["modelo"]))
    return {"salvo": True, "avaliacao": avaliacao, "categoria": categoria}


def contexto_loja(usuario, pedido_id):
    """Autorização em cada turno; somente campos operacionais seguem para a IA."""
    with conexao_web() as conn:
        pedido = conn.execute("""
            SELECT status, uf, prazo_prometido, prazo_original, eventos, responsavel_id
            FROM pedidos_demo WHERE id=%s AND (%s='analista' OR cliente_id=%s)
        """, (pedido_id, usuario["perfil"], usuario["id"])).fetchone()
    if pedido is None:
        raise HTTPException(404, "Pedido não encontrado.")
    eventos = []
    for evento in pedido["eventos"][-15:]:
        publico = {k: evento[k] for k in ("em", "status") if k in evento}
        publico["alteracoes"] = [{k: a.get(k) for k in ("campo", "anterior", "novo")}
                                for a in evento.get("alteracoes", []) if a.get("campo") == "prazo_prometido"]
        eventos.append(publico)
    return {
        "publico": usuario["perfil"],
        "fonte_dados": "loja",
        "contexto": "Pedido selecionado na loja demonstrativa — sem pagamento ou envio real",
        "pedido": {"status": pedido["status"], "uf": pedido["uf"],
                   "prazo_prometido": str(pedido["prazo_prometido"]) if pedido["prazo_prometido"] else None,
                   "prazo_original": str(pedido["prazo_original"]) if pedido["prazo_original"] else None,
                   "responsavel_atribuido": pedido["responsavel_id"] is not None,
                   "historico_recente": eventos},
        "limites": "Este pedido demonstrativo não tem previsão individual de ML. Prazo nulo significa não definido. Nomes e justificativas internas não são enviados.",
    }
