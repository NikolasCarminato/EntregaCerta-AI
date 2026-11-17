# Iniciar a aplicação pela raiz: .\.venv\Scripts\python.exe main.py iniciar
#.\.venv\Scripts\python.exe -m uvicorn src.api:app --reload --host 127.0.0.1 --port 8000

from pydantic import BaseModel, Field, ConfigDict
from .assistente_genai import responder
import logging
from datetime import date, datetime, timezone
from threading import Lock
from typing import Literal
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4
import json
from . import conversas_assistente as conversas

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from .acesso_web import politica_acesso, router as router_acesso
from .loja_web import router as router_loja
from .relatorio_pdf import registrar_analise, gerar_relatorio

from .banco import consultar_pedidos
from .pedidos_web import montar_lote, filtrar_lote
from .inferencia import (
    carregar_modelos,
    prever_pedidos,
    priorizar_pedidos,
)

app = FastAPI(
    title="EntregaCerta AI",
    description="Priorização de pedidos com risco de atraso.",
    version="0.2.0",
    dependencies=[Depends(politica_acesso)],
    docs_url=None, redoc_url=None, openapi_url=None,
)
app.include_router(router_acesso)
app.include_router(router_loja)


@app.exception_handler(RequestValidationError)
async def dados_invalidos(request, erro):
    # Não devolve senhas ou outros campos enviados junto às mensagens de validação.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in erro.errors()
    ]})

PASTA_FRONTEND = Path(__file__).resolve().parents[1] / "frontend"

class AssetsPublicos(StaticFiles):
    async def get_response(self, path, scope):
        # HTML privado, ZIPs de versões e arquivos de dados não são assets públicos.
        if Path(path).suffix.lower() not in {".css", ".js", ".svg", ".png", ".jpg", ".webp", ".woff", ".woff2", ".ico"}:
            raise HTTPException(404, "Arquivo não encontrado.")
        return await super().get_response(path, scope)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "same-origin"
    if not request.url.path.startswith("/static/"):
        resposta.headers["Cache-Control"] = "no-store"
    return resposta


app.mount(
    "/static",
    AssetsPublicos(directory=PASTA_FRONTEND),
    name="static",
)


@app.get("/", include_in_schema=False)
def pagina_inicial():
    return FileResponse(PASTA_FRONTEND / "index.html")


@app.get("/cliente", include_in_schema=False)
def pagina_cliente():
    return FileResponse(PASTA_FRONTEND / "cliente.html")


@app.get("/entrar", include_in_schema=False)
def pagina_login():
    return FileResponse(PASTA_FRONTEND / "entrar.html")


@app.get("/dashboard", include_in_schema=False)
def pagina_dashboard():
    return FileResponse(PASTA_FRONTEND / "dashboard.html")


@app.get("/pedidos", include_in_schema=False)
def pagina_pedidos():
    return FileResponse(PASTA_FRONTEND / "pedidos.html")


@lru_cache(maxsize=1)
def obter_modelos():
    # Evita carregar os mesmos arquivos a cada consulta.
    return carregar_modelos()


def entradas_modelo(pacote):
    if pacote.get('tipo') == 'catboost_ver07':
        from .catboost_ver07 import consultar
        return consultar(pacote)
    return consultar_pedidos('validacao')


def descricao_modelo(pacote, resultado):
    if pacote.get('tipo') == 'catboost_ver07':
        from .catboost_ver07 import descrever
        return descrever(pacote, resultado)
    return {'contexto': 'Demonstração histórica — validação Olist',
            'modelo': 'MLP anterior', 'politica_fila': 'fracao_lote',
            'base_capacidade': 'do lote completo',
            'regra': 'Selecionar a fração configurada do lote por score decrescente.',
            'observacao': 'O score indica risco relativo, não uma probabilidade calibrada.'}


# Um lote em memória por processo; filtros não consultam nem pontuam novamente.
_trava_lote = Lock()


@lru_cache(maxsize=1)
def obter_lote_pedidos():
    pacote, modelo = obter_modelos()
    pedidos = entradas_modelo(pacote)
    previsoes = priorizar_pedidos(prever_pedidos(pedidos, pacote, modelo), pacote["fracao_fila"])
    lote = montar_lote(pedidos, previsoes)
    lote.attrs['modelo'] = descricao_modelo(pacote, previsoes)
    return lote, datetime.now(timezone.utc).isoformat(), float(pacote["fracao_fila"])


@app.get("/api/pedidos")
def consultar_lista_pedidos(
    pagina: int = Query(1, ge=1),
    tamanho: int = Query(25, ge=25, le=100),
    busca: str = Query("", max_length=100),
    uf: str = Query("", max_length=2),
    categoria: str = Query("", max_length=100),
    prioridade: Literal["todos", "0", "1"] = "todos",
    inicio: date | None = None,
    fim: date | None = None,
    score_min: float = Query(0, ge=0, le=1),
    score_max: float = Query(1, ge=0, le=1),
    ordem: Literal["score-desc", "score-asc", "data-desc", "data-asc", "valor-desc"] = "score-desc",
    atualizar: bool = False,
):
    if tamanho not in (25, 50, 100):
        raise HTTPException(422, "Escolha 25, 50 ou 100 pedidos por página.")
    if score_min > score_max or (inicio and fim and inicio > fim):
        raise HTTPException(422, "O início do intervalo deve ser menor ou igual ao fim.")
    try:
        with _trava_lote:
            if atualizar:
                obter_lote_pedidos.cache_clear()
            lote, consultado_em, fracao = obter_lote_pedidos()
        resposta = filtrar_lote(
            lote, pagina=pagina, tamanho=tamanho, busca=busca, uf=uf.upper(),
            categoria=categoria, prioridade=prioridade, inicio=inicio, fim=fim,
            score_min=score_min, score_max=score_max, ordem=ordem,
        )
        return {**resposta, "consultado_em": consultado_em, "fracao_fila": fracao,
                **lote.attrs.get('modelo', {'contexto': 'Demonstração histórica — validação Olist'})}
    except Exception:
        logging.exception("Falha ao consultar o catálogo de pedidos.")
        raise HTTPException(503, "Não foi possível consultar os pedidos. Tente atualizar o lote.") from None


@app.get("/api/prioridades")
def consultar_prioridades(request: Request = None):
    try:
        # Consulta as entradas da coorte compatível com o modelo ativo.
        pacote, modelo = obter_modelos()
        pedidos = entradas_modelo(pacote)
        previsoes = prever_pedidos(pedidos, pacote, modelo)
        resultado = priorizar_pedidos(
            previsoes,
            pacote["fracao_fila"],
        )

        # 3. Seleciona a fila e organiza por score.
        fila = resultado.loc[
            resultado["prioridade"].eq(1)
        ].sort_values(
            ["score_atraso", "order_id"],
            ascending=[False, True],
        )

        # 4. Devolve os dados para o frontend.
        dados = {
            **descricao_modelo(pacote, resultado),
            "pedidos_avaliados": len(resultado),
            "pedidos_na_fila": len(fila),
            "fracao_fila": float(pacote["fracao_fila"]),
            "fila": fila[['order_id', 'score_atraso', 'prioridade']].to_dict(orient="records"),
        }
        if request is not None:
            dados.update(registrar_analise(dados, request.state.usuario["id"]))
        return dados

    except Exception:
        logging.exception("Falha ao consultar e priorizar pedidos.")
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel gerar a fila de pedidos.",
        ) from None

class PerguntaAssistente(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pergunta: str = Field(min_length=1, max_length=1000)
    conversa_id: UUID | None = None
    requisicao_id: UUID = Field(default_factory=uuid4)


class PedidoAssistente(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["loja", "olist"]
    id: str = Field(pattern=r"^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$")


class NovaConversa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pedido: PedidoAssistente | None = None


class AvaliacaoAssistente(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversa_id: UUID
    resposta_id: UUID
    avaliacao: Literal[-1, 1]
    categoria: Literal["incorreta", "confusa", "nao_respondeu", "outro"] | None = None


def contexto_assistente(usuario, pedido=None):
    if pedido:
        if pedido["tipo"] == "loja":
            return conversas.contexto_loja(usuario, pedido["id"])
        if usuario["perfil"] != "analista":
            raise HTTPException(403, "Este contexto é exclusivo do analista.")
        lote, consultado_em, _ = obter_lote_pedidos()
        registro = lote.loc[lote["order_id"].eq(pedido["id"])].drop(columns="order_id")
        if registro.empty:
            raise HTTPException(404, "Pedido não encontrado.")
        return {"publico": "analista", "fonte_dados": "olist", "contexto": lote.attrs.get('modelo', {}).get('contexto', 'Pedido histórico Olist'),
                "pedido": json.loads(registro.to_json(orient="records", date_format="iso"))[0],
                "consultado_em": consultado_em,
                "limites": "Score é risco relativo, não probabilidade calibrada. Atributos não comprovam causas do atraso. Zero para pedido não pontuado é convenção, não estimativa de risco. UF de origem e categoria usam vendedor/item de referência na VER0.7. Sem previsão de data individual ou alteração de pedido."}
    if usuario["perfil"] == "cliente":
        return {
            "publico": "cliente",
            "fonte_dados": "orientacoes",
            "contexto": "Área do cliente — loja demonstrativa EntregaCerta AI",
            "funcionalidades": "Catálogo, carrinho, meus pedidos, prazo prometido e histórico de etapas e prazos.",
            "prazo": "Informado pela equipe; se não definido, não há uma data prometida disponível.",
            "limites": "Sem pagamento ou envio real. Nenhum pedido está selecionado nesta conversa. Em Meus pedidos, use Conversar sobre pedido para consultar os dados registrados. Sem previsão de ML para pedidos demonstrativos.",
        }

    # O resumo continua sendo calculado no servidor, sem números enviados pelo browser.
    dados = consultar_prioridades()
    return {
        "publico": "analista",
        "fonte_dados": "fila",
        "contexto": dados["contexto"],
        "pedidos_avaliados": dados["pedidos_avaliados"],
        "pedidos_na_fila": dados["pedidos_na_fila"],
        "percentual_configurado_da_fila": (
            100 * dados["fracao_fila"]
        ),
        "observacao": dados["observacao"],
        "regra": dados.get('regra', 'A fila prioriza os pedidos com maiores scores do modelo.'),
        **{k: dados[k] for k in ('politica_fila', 'base_capacidade', 'pendentes_avaliados', 'limites_diarios', 'modelo') if k in dados},
    }

@app.post("/api/assistente/conversas")
def nova_conversa(dados: NovaConversa, request: Request):
    pedido = dados.pedido.model_dump() if dados.pedido else None
    # Valida a permissão antes de criar memória para um pedido.
    if pedido:
        contexto_assistente(request.state.usuario, pedido)
    return {"conversa_id": conversas.criar(request.state.usuario, pedido)}


@app.delete("/api/assistente/conversas/{conversa_id}")
def apagar_conversa(conversa_id: UUID, request: Request):
    conversas.apagar(conversa_id, request.state.usuario)
    return {"apagada": True}


@app.post("/api/assistente")
def conversar_assistente(mensagem: PerguntaAssistente, request: Request):
    pergunta = mensagem.pergunta.strip()
    if not pergunta:
        raise HTTPException(400, "Digite uma pergunta.")
    usuario = request.state.usuario
    identificador = mensagem.conversa_id or conversas.criar(usuario)
    conversa = conversas.obter(identificador, usuario)
    contexto = contexto_assistente(usuario, conversa.pedido)
    return conversas.conversar(identificador, usuario, mensagem.requisicao_id, pergunta, contexto, responder)


@app.post("/api/assistente/avaliacoes")
def avaliar_assistente(dados: AvaliacaoAssistente, request: Request):
    return conversas.avaliar(request.state.usuario, dados.conversa_id, dados.resposta_id,
                            dados.avaliacao, dados.categoria)


class PedidoRelatorio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analise_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


@app.post("/api/analista/relatorio-pdf")
def baixar_relatorio(dados: PedidoRelatorio, request: Request):
    # A política global exige analista, sessão, origem permitida e CSRF.
    # Só aceita o identificador; números e scores vêm do snapshot privado do servidor.
    try:
        pdf, genai = gerar_relatorio(dados.analise_id, request.state.usuario["id"])
    except HTTPException:
        raise
    except Exception:
        logging.exception("Falha ao montar o relatório PDF.")
        raise HTTPException(503, "Não foi possível gerar o relatório. Tente novamente.") from None
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="entregacerta-relatorio.pdf"',
        "X-Resumo-GenAI": "incluido" if genai else "indisponivel",
    })
