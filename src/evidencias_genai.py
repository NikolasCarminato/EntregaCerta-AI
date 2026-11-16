"""Evidências extraídas do contexto autorizado, sem geração de fatos pela LLM."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo


# Lista explícita: dados internos ou campos novos não são publicados automaticamente.
CAMPOS = [
    ("contexto", "Cenário", "texto"),
    ("pedidos_avaliados", "Pedidos avaliados", "numero"),
    ("pedidos_na_fila", "Pedidos na fila", "numero"),
    ("percentual_configurado_da_fila", "Capacidade configurada da fila (%)", "numero"),
    ("pedido.status", "Etapa registrada", "etapa"),
    ("pedido.prazo_prometido", "Prazo prometido atual", "data"),
    ("pedido.prazo_original", "Prazo original", "data"),
    ("pedido.uf", "UF de destino", "texto"),
    ("pedido.responsavel_atribuido", "Responsável atribuído", "booleano"),
    ("pedido.historico_recente", "Histórico recente de etapas e prazos (até 15 eventos)", "historico"),
    ("pedido.order_approved_at", "Aprovação do pedido", "data_hora"),
    ("pedido.customer_state", "UF de destino", "texto"),
    ("pedido.uf_origem", "UF de origem", "texto"),
    ("pedido.categoria_principal", "Categoria principal", "texto"),
    ("pedido.quantidade_itens", "Quantidade de itens", "numero"),
    ("pedido.quantidade_vendedores", "Quantidade de vendedores", "numero"),
    ("pedido.valor_itens", "Valor dos itens (R$)", "numero"),
    ("pedido.valor_frete", "Frete (R$)", "numero"),
    ("pedido.valor_pago", "Valor pago (R$)", "numero"),
    ("pedido.peso_total_g", "Peso total (g)", "numero"),
    ("pedido.distancia_media_vendedores_km", "Distância média dos vendedores (km)", "numero"),
    ("pedido.distancia_max_vendedores_km", "Distância máxima dos vendedores (km)", "numero"),
    ("pedido.prazo_prometido_dias", "Prazo prometido (dias)", "numero"),
    ("pedido.tipo_pagamento_principal", "Pagamento principal", "texto"),
    ("pedido.parcelas_maximas", "Parcelas máximas", "numero"),
    ("pedido.score_atraso", "Score de risco relativo (não é probabilidade)", "numero"),
    ("pedido.prioridade", "Na fila de prioridade", "booleano"),
    ("funcionalidades", "Funcionalidades disponíveis", "texto"),
    ("prazo", "Regra do prazo", "texto"),
    ("observacao", "Interpretação do score", "texto"),
    ("regra", "Regra de priorização", "texto"),
    ("limites", "Limites do contexto", "texto"),
    # Acrescentar ao fim para preservar as referências E1–E32 das conversas.
    ("data_referencia", "Data de referência da consulta", "data"),
    ("fuso_referencia", "Fuso da data de referência", "texto"),
    ("capacidades_aplicacao", "Recursos disponíveis para este perfil", "texto"),
    ("etapas_aplicacao", "Significado das etapas demonstrativas", "texto"),
    ("calendario_prazo_atual", "Calendário do prazo atual (cálculo do servidor)", "texto"),
    ("calendario_prazo_original", "Calendário do prazo original (cálculo do servidor)", "texto"),
    ("pedidos_fora_fila", "Pedidos fora da fila (total menos selecionados)", "numero"),
    ("informacoes_ausentes", "Informações não fornecidas nesta consulta", "texto"),
    ("simulacao_fila", "Simulação solicitada da capacidade (cálculo do servidor)", "texto"),
]
FONTES = {
    "loja": "Loja demonstrativa · registros autorizados do PostgreSQL",
    "olist": "Olist · pedido histórico da validação (registros de 2016–2018)",
    "fila": "Olist · resumo da fila histórica de validação (2016–2018)",
    "orientacoes": "Orientações e regras da aplicação · sem pedido selecionado",
}
REFERENCIA = re.compile(r"\[(E\d+(?:\s*[,;]\s*E\d+)*)\]")


def calendario_prazo(valor, referencia, rotulo):
    """Compara dias de calendário, sem inferir desfecho ou previsão de entrega."""
    if valor is None:
        return f"{rotulo}: não informado. Não confirma promessa vigente nem mudança no histórico."
    try:
        prazo = date.fromisoformat(str(valor))
        primeiro_dia_ultrapassado = prazo + timedelta(days=1)
    except (ValueError, TypeError, OverflowError):
        return f"{rotulo}: data inválida para comparação; não é possível concluir se foi ultrapassado."
    dias = (referencia - prazo).days
    if dias < 0:
        situacao = f"ainda não ultrapassado; faltam {-dias} dias para o vencimento"
    elif dias == 0:
        situacao = "vence hoje; ainda NÃO foi ultrapassado"
    else:
        situacao = f"prazo ultrapassado há {dias} dia(s)"
    return (
        f"{rotulo}: {prazo.isoformat()}. Até {prazo.isoformat()} inclusive: NÃO ultrapassado. "
        f"Somente a partir de {primeiro_dia_ultrapassado.isoformat()}: ultrapassado. "
        f"Em {referencia.isoformat()}: {situacao}. "
        "Esta é uma comparação de calendário, não uma data prevista nem dias de atraso da entrega efetiva."
    )


def simular_capacidade(pergunta, total):
    """Só interpreta um percentual explicitamente ligado à capacidade total."""
    if type(total) is not int or total < 0:
        return None
    if re.search(r"priorizad|selecionad|fila atual|pedidos da fila", pergunta, re.I):
        return None  # Subgrupo explícito: não presumir que a base é o lote inteiro.
    valores = re.findall(r"(?<![\d.,-])(\d+(?:[.,]\d+)?)\s*%", pergunta)
    if len(valores) != 1 or not re.search(
        r"(?:fila\s+(?:para|de)|capacidade(?:\s+de)?|revisar)\s+\d+(?:[.,]\d+)?\s*%", pergunta, re.I
    ):
        return None  # Pergunta ambígua ou vários percentuais: não inventar intenção.
    percentual = Decimal(valores[0].replace(',', '.'))
    if not 0 <= percentual <= 100:
        return None
    exato = Decimal(total) * percentual / 100
    quantidade = int(exato.to_integral_value(rounding=ROUND_FLOOR))
    return (
        f"Hipótese solicitada na pergunta: {percentual}% do total de {total} pedidos avaliados. "
        f"Cálculo: {total} x {percentual} / 100 = {exato}; arredondando para baixo: {quantidade} pedidos. "
        "A base é o lote inteiro, não a fila atualmente selecionada. "
        "Esta simulação não altera a fila e não mede precisão, recall ou atrasos evitados."
    )


def contextualizar(contexto, perfil, *, agora=None, pergunta=""):
    """Acrescenta fatos do servidor; o perfil vem da sessão autenticada."""
    contexto = deepcopy(contexto)
    instante = agora if agora is not None else datetime.now(timezone.utc)
    if instante.tzinfo is None:
        raise ValueError("A data de referência precisa conter fuso horário.")
    contexto["publico"] = perfil
    contexto["fuso_referencia"] = "America/Sao_Paulo"
    contexto["data_referencia"] = instante.astimezone(ZoneInfo(contexto["fuso_referencia"])).date().isoformat()
    recursos = (
        "Este chat apenas consulta e explica o contexto recebido; não executa ações nem anexa arquivos. "
        "Meus pedidos mostra registros demonstrativos. Conversar sobre pedido apenas abre uma consulta; "
        "não cancela, não reembolsa e não altera pedidos. Não há função de cancelamento ou reembolso "
        "implementada neste site; não existe botão ou caminho de cancelamento para indicar. "
        "O rastreio é demonstrativo, sem consulta a transportadoras reais. "
    )
    if perfil == "analista":
        recursos += (
            "O analista pode gerar PDF no Dashboard (/dashboard): primeiro Gerar fila de prioridade, "
            "depois Gerar relatório em PDF. O relatório cobre a análise do dashboard e é baixado pelo "
            "navegador, não por esta conversa. O botão só fica habilitado após carregar a análise. "
            "O PDF não é relatório individual do pedido demonstrativo."
        )
    else:
        recursos += (
            "Este perfil de cliente não pode gerar o relatório PDF do dashboard; esse recurso é exclusivo "
            "de analistas. O chat também não oferece exportação de PDF para o cliente."
        )
    contexto["capacidades_aplicacao"] = recursos
    # Não transportar cálculos anteriores para um novo turno ou outro cenário.
    for campo in ("calendario_prazo_atual", "calendario_prazo_original", "etapas_aplicacao"):
        contexto.pop(campo, None)
    if contexto.get("fonte_dados") == "loja":
        pedido = contexto.get("pedido") or {}
        referencia = date.fromisoformat(contexto["data_referencia"])
        contexto["calendario_prazo_atual"] = calendario_prazo(pedido.get("prazo_prometido"), referencia, "Prazo atual")
        contexto["calendario_prazo_original"] = calendario_prazo(pedido.get("prazo_original"), referencia, "Prazo original")
        contexto["etapas_aplicacao"] = (
            "São etapas registradas da demonstração, não rastreamento físico: recebido = pedido registrado; "
            "em_preparacao = etapa de preparação registrada, sem confirmar separação ou embalagem física; "
            "despachado = etapa de despacho registrada, sem indicar centro de distribuição, localização, "
            "transportadora, saída para entrega ou chegada hoje; entregue = entrega registrada, "
            "sem comprovar pontualidade se não houver data de entrega."
        )
    for campo in ("pedidos_fora_fila", "informacoes_ausentes", "simulacao_fila"):
        contexto.pop(campo, None)
    if contexto.get("fonte_dados") in ("fila", "olist"):
        pedido = contexto.get("pedido") or {}
        faltantes = []
        grupos = [
            (("atrasou", "order_delivered_customer_date", "data_entrega"), "desfecho e data efetiva de entrega"),
            (("precision", "recall", "f1", "accuracy", "metricas"), "métricas de desempenho do modelo"),
            (("economia", "roi", "ganho_financeiro"), "economia financeira medida"),
            (("shap", "contribuicoes"), "contribuição individual das variáveis"),
        ]
        for campos, descricao in grupos:
            if not any(contexto.get(c) is not None or pedido.get(c) is not None for c in campos):
                faltantes.append(descricao)
        if faltantes:
            contexto["informacoes_ausentes"] = (
                "Não fornecidos nesta consulta: " + "; ".join(faltantes) + ". "
                "Isso não afirma que esses dados não existam em outras partes do projeto."
            )
    if contexto.get("fonte_dados") == "fila":
        total, fila = contexto.get("pedidos_avaliados"), contexto.get("pedidos_na_fila")
        if type(total) is int and type(fila) is int and 0 <= fila <= total:
            contexto["pedidos_fora_fila"] = total - fila
        simulacao = simular_capacidade(pergunta, total)
        if simulacao is not None:
            contexto["simulacao_fila"] = simulacao
    return contexto


def preparar(contexto):
    """Congela os campos recebidos e registra quando foram preparados para a IA."""
    itens = []
    for indice, (campo, rotulo, formato) in enumerate(CAMPOS, 1):
        valor = contexto
        for parte in campo.split("."):
            if not isinstance(valor, dict) or parte not in valor:
                break
            valor = valor[parte]
        else:
            if formato == "historico":
                # Segunda barreira: autores, IDs e notas internas nunca aparecem aqui.
                valor = [{"em": e.get("em"), "status": e.get("status"),
                          "alteracoes": [{k: a.get(k) for k in ("campo", "anterior", "novo")}
                                         for a in e.get("alteracoes", []) if a.get("campo") == "prazo_prometido"]}
                         for e in (valor or [])[-15:]]
            itens.append({"id": f"E{indice}", "campo": campo, "rotulo": rotulo,
                          "valor": deepcopy(valor), "formato": formato})
    return {
        "origem": FONTES.get(contexto.get("fonte_dados"), "Contexto fornecido pela aplicação"),
        "consultado_em": datetime.now(timezone.utc).isoformat(),
        "lote_carregado_em": contexto.get("consultado_em"),
        "itens": itens,
    }


def contexto_com_evidencias(contexto, evidencias):
    return {**deepcopy(contexto), "evidencias_disponiveis": [
        {"id": e["id"], "campo": e["campo"], "dado": e["rotulo"], "valor": e["valor"]} for e in evidencias["itens"]
    ]}


def conferir_referencias(texto, evidencias):
    """Confere existência da referência. Não verifica a verdade de toda a frase."""
    citadas = list(dict.fromkeys(ref for grupo in REFERENCIA.findall(texto) for ref in re.findall(r"E\d+", grupo)))
    permitidas = {e["id"] for e in evidencias["itens"]}
    return citadas, [r for r in citadas if r not in permitidas]
