"""Inferência CatBoost D-1 e carga histórica isolada no PostgreSQL.

Os atributos foram reconstruídos com o código do pacote e conferidos contra
as previsões recebidas. A API nunca lê alvos nem scores pré-calculados.
"""
from pathlib import Path
import hashlib
import json
import math
import os

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from psycopg.types.json import Jsonb

from .banco import obter_conexao

PASTA = Path(__file__).resolve().parents[1] / 'Modelos' / 'catboost_ver07'
SCHEMA = 'modelo_ver07'
META = ['order_id', 'particao', 'ordem_original', 'checkpoint_at', 'pendente', 'elegivel',
        'order_purchase_timestamp', 'order_estimated_delivery_date', 'order_approved_at',
        'customer_state', 'uf_origem', 'categoria_principal', 'quantidade_itens',
        'quantidade_vendedores', 'valor_itens', 'valor_frete', 'valor_pago', 'peso_total_g',
        'distancia_vendedor_referencia_km', 'prazo_prometido_dias',
        'tipo_pagamento_principal', 'parcelas_maximas']


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def carregar():
    arquivo = PASTA / 'config.json'
    if not arquivo.is_file():
        raise FileNotFoundError('Prepare o CatBoost conforme Docs/modelo_ver07.md.')
    pacote = json.loads(arquivo.read_text(encoding='utf-8'))
    if pacote['tipo'] != 'catboost_ver07' or pacote['politica'] != 'cota_diaria_pendentes':
        raise ValueError('Configuração CatBoost desconhecida.')
    caminho = PASTA / 'modelo.cbm'
    if sha256(caminho) != pacote['model_sha256']:
        raise ValueError('O modelo CatBoost difere do artefato verificado.')
    modelo = CatBoostClassifier()
    modelo.load_model(str(caminho))
    if modelo.feature_names_ != pacote['colunas_entrada']:
        raise ValueError('Contrato de atributos incompatível com o .cbm.')
    categorias = [modelo.feature_names_[i] for i in modelo.get_cat_feature_indices()]
    if categorias != pacote['colunas_categoricas']:
        raise ValueError('Contrato de categorias incompatível com o .cbm.')
    return pacote, modelo


def particao(pacote):
    valor = os.getenv('ENTREGACERTA_PARTICAO', pacote['particao_padrao'])
    if valor not in ('validacao', 'teste'):
        raise ValueError('ENTREGACERTA_PARTICAO deve ser validacao ou teste.')
    return valor


def carregar_banco():
    """Primeira carga atômica em schema próprio. Nunca sobrescreve outra carga."""
    pacote, _ = carregar()
    caminho = PASTA / pacote['dados']
    if sha256(caminho) != pacote['dados_sha256']:
        raise ValueError('Arquivo de atributos mudou após a reprodução.')
    df = pd.read_parquet(caminho)
    esperadas = sum(pacote['contagens'].values())
    if len(df) != esperadas or not df.order_id.is_unique:
        raise ValueError('Contagem ou chaves inválidas no lote CatBoost.')
    features = pacote['colunas_entrada']
    # json.dumps de Pandas converte faltantes em null sem inventar valores.
    attrs = json.loads(df[features].to_json(orient='records', double_precision=15))
    meta = json.loads(df[META].to_json(orient='records', date_format='iso', double_precision=15))
    with obter_conexao() as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS modelo_ver07')
        conn.execute('''CREATE TABLE IF NOT EXISTS modelo_ver07.pedidos (
            order_id text PRIMARY KEY, particao text NOT NULL CHECK (particao IN ('validacao','teste')),
            ordem_original integer NOT NULL UNIQUE, dados_sha256 text NOT NULL,
            atributos jsonb NOT NULL, contexto jsonb NOT NULL)''')
        existente = conn.execute('SELECT count(*), min(dados_sha256), max(dados_sha256) FROM modelo_ver07.pedidos').fetchone()
        if existente[0]:
            if existente == (len(df), pacote['dados_sha256'], pacote['dados_sha256']):
                return {'pedidos': len(df), 'ja_carregado': True}
            raise ValueError('Carga VER0.7 diferente já existe. Preservada; use outra versão/schema.')
        with conn.cursor().copy('COPY modelo_ver07.pedidos (order_id,particao,ordem_original,dados_sha256,atributos,contexto) FROM STDIN') as copy:
            for a, m in zip(attrs, meta):
                copy.write_row((m['order_id'], m['particao'], m['ordem_original'], pacote['dados_sha256'], Jsonb(a), Jsonb(m)))
    return {'pedidos': len(df), 'ja_carregado': False}


def consultar(pacote):
    """Lê entradas, não alvos ou previsões. Mantém desempate do experimento."""
    with obter_conexao() as conn:
        registros = conn.execute('''SELECT atributos, contexto, dados_sha256
            FROM modelo_ver07.pedidos WHERE particao=%s ORDER BY ordem_original''',
            (particao(pacote),)).fetchall()
    if len(registros) != pacote['contagens'][particao(pacote)]:
        raise ValueError('Lote CatBoost incompleto. Execute python main.py preparar-catboost.')
    if any(h != pacote['dados_sha256'] for _, _, h in registros):
        raise ValueError('Versão das entradas PostgreSQL incompatível com o modelo.')
    rows = []
    for attrs, meta, _ in registros:
        if set(attrs) != set(pacote['colunas_entrada']) or set(meta) != set(META):
            raise ValueError('Contrato de entradas PostgreSQL alterado.')
        # Features têm precedência para preservar categorias e valores treinados.
        rows.append({**meta, **attrs})
    return pd.DataFrame(rows)


def prever(pedidos, pacote, modelo):
    required = set(pacote['colunas_entrada']) | {'order_id', 'checkpoint_at', 'ordem_original', 'pendente', 'elegivel'}
    missing = required - set(pedidos)
    if missing:
        raise ValueError('Entradas D-1 ausentes: ' + ', '.join(sorted(missing)))
    if pedidos.order_id.isna().any() or not pedidos.order_id.is_unique or not pedidos.ordem_original.is_unique:
        raise ValueError('Pedidos e ordem de desempate precisam ser únicos.')
    if not pedidos[['pendente', 'elegivel']].isin([True, False, 0, 1]).all().all():
        raise ValueError('Flags de elegibilidade inválidas.')
    resultado = pedidos[['order_id', 'checkpoint_at', 'ordem_original', 'pendente', 'elegivel']].copy().reset_index(drop=True)
    resultado['checkpoint_at'] = pd.to_datetime(resultado.checkpoint_at, errors='raise')
    if resultado.checkpoint_at.isna().any():
        raise ValueError('Checkpoint ausente.')
    resultado['pendente'] = resultado.pendente.astype(bool) & resultado.elegivel.astype(bool)
    ativo = resultado.pendente.to_numpy()
    X = pedidos[pacote['colunas_entrada']].reset_index(drop=True).copy()
    for c in X:
        if c in pacote['colunas_categoricas']:
            X[c] = X[c].fillna('__MISSING__').astype(str)
        else:
            X[c] = pd.to_numeric(X[c], errors='raise')
            if np.isinf(X[c].to_numpy(dtype=float)).any():
                raise ValueError('Atributo infinito: ' + c)
    scores = np.zeros(len(pedidos), dtype=float)
    if ativo.any():
        scores[ativo] = modelo.predict_proba(X.loc[ativo], thread_count=4)[:, 1]
    if not np.isfinite(scores).all() or not ((scores >= 0) & (scores <= 1)).all():
        raise ValueError('CatBoost retornou scores inválidos.')
    resultado['score_atraso'] = scores
    resultado['situacao_modelo'] = np.where(ativo, 'pontuado_em_D-1',
        np.where(resultado.elegivel, 'entregue_antes_do_checkpoint', 'fora_do_checkpoint'))
    return resultado


def priorizar(previsoes, fracao):
    if not np.isfinite(fracao) or not 0 <= fracao <= 1:
        raise ValueError('Fração inválida.')
    out = previsoes.copy().reset_index(drop=True)
    out['prioridade'] = 0
    pendentes = out.loc[out.pendente & out.elegivel].copy()
    pendentes['dia'] = pd.to_datetime(pendentes.checkpoint_at).dt.normalize()
    for _, grupo in pendentes.groupby('dia', sort=False):
        k = math.ceil(len(grupo) * fracao)
        escolhidos = grupo.sort_values(['score_atraso', 'ordem_original'], ascending=[False, True], kind='stable').head(k).index
        out.loc[escolhidos, 'prioridade'] = 1
    return out


def descrever(pacote, resultado):
    """Denominador e capacidade explícitos para interface, PDF e GenAI."""
    dias = []
    ativos = resultado.loc[resultado.pendente].copy()
    for dia, grupo in ativos.groupby(pd.to_datetime(ativos.checkpoint_at).dt.date, sort=True):
        dias.append({'data': dia.isoformat(), 'pendentes': len(grupo), 'selecionados': int(grupo.prioridade.sum())})
    return {
        'modelo': 'CatBoost VER0.7 piloto', 'politica_fila': 'cota_diaria_pendentes',
        'contexto': f'Demonstração histórica Olist — {particao(pacote)} VER0.7 — D−1 (exploratório)',
        'base_capacidade': 'dos pedidos pendentes de cada dia em D−1, arredondando para cima',
        'pendentes_avaliados': len(ativos), 'nao_pontuados': len(resultado) - len(ativos),
        'limites_diarios': dias,
        'regra': 'Em cada dia de checkpoint D−1, selecionar ceil(0,65 × pendentes do dia), por score decrescente. Pedidos já entregues não são pontuados e recebem zero convencional. A ordem por score na tela não altera as cotas diárias.',
        'observacao': 'Score é risco relativo, não probabilidade calibrada. Piloto exploratório: teste reutilizado e cota de 65% escolhida após exploração. Não comprova generalização ou redução real de atrasos.',
    }


def validar_cota(dados):
    dias = dados.get('limites_diarios')
    if not isinstance(dias, list):
        return False
    vistos, pendentes, alertas = set(), 0, 0
    for d in dias:
        if not isinstance(d, dict) or d.get('data') in vistos:
            return False
        n, k = d.get('pendentes'), d.get('selecionados')
        if type(n) is not int or type(k) is not int or n < 1 or k != math.ceil(n * dados['fracao_fila']):
            return False
        vistos.add(d.get('data')); pendentes += n; alertas += k
    return (pendentes == dados.get('pendentes_avaliados') and
            0 <= pendentes <= dados['pedidos_avaliados'] and alertas == dados['pedidos_na_fila'])
