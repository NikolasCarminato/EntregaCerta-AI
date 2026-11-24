"""Pesquisa real Olist: maximizar recall, com no máximo 30% dos pedidos revisados.

python -m experimentos.testar_fila30 --fase preparar|treinar|avaliar
Usar .venv-ctgan. Saídas isoladas; não altera PostgreSQL nem modelos do site.
Ranking por coorte é uma avaliação retrospectiva de capacidade, não um escalonador
online. A fila também é medida entre os pedidos ainda pendentes, sem esconder
o efeito de entregas já observadas sobre o denominador.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from src.preparar_dados import RAIZ, COLUNAS_PREVISAO, PASTA_ORIGINAIS
from src.tratar_dados_ml import NUMERICAS, CATEGORICAS, sha256
from .testar_historicos_vendedores import COLUNAS_HISTORICO, criar_historicos
from .testar_previsao_operacional import OPERACIONAIS, DOBRAS, divisao_temporal

SAIDA = RAIZ / 'Modelos/experimento_fila30_20261116'
FRACAO = .30
ETAPAS_ML = ['aprovacao', 'despacho', 'd14']
ETAPAS_REGRAS = ['d14', 'd10', 'd7', 'd3', 'd1']
NOVAS = ['excesso_transito_historico', 'proporcao_transito_historico',
         'distancia_por_dia_restante', 'mes_consulta', 'dia_semana_consulta']
CONFIGS = [
    {'nome': 'LR_congelado', 'tipo': 'LR', 'historico': 'congelado', 'C': 1.},
    {'nome': 'LR_atual_C01', 'tipo': 'LR', 'historico': 'atual', 'C': .1},
    {'nome': 'LR_atual_C1', 'tipo': 'LR', 'historico': 'atual', 'C': 1.},
    {'nome': 'LR_atual_C10', 'tipo': 'LR', 'historico': 'atual', 'C': 10.},
    {'nome': 'LR_recente180', 'tipo': 'LR', 'historico': 'atual', 'C': 1., 'janela': 180},
    {'nome': 'LR_balanceada', 'tipo': 'LR', 'historico': 'atual', 'C': 1., 'balanceado': True},
    {'nome': 'HGB_regularizado', 'tipo': 'HGB', 'historico': 'atual', 'folhas': 7, 'min_folha': 100},
    {'nome': 'HGB_atual', 'tipo': 'HGB', 'historico': 'atual', 'folhas': 15, 'min_folha': 40},
    {'nome': 'HGB_recente180', 'tipo': 'HGB', 'historico': 'atual', 'folhas': 15, 'min_folha': 40, 'janela': 180},
    {'nome': 'CatBoost_d4', 'tipo': 'CAT', 'historico': 'atual', 'profundidade': 4},
    {'nome': 'CatBoost_d6', 'tipo': 'CAT', 'historico': 'atual', 'profundidade': 6},
    {'nome': 'MLP_sementes7_42', 'tipo': 'MLP', 'historico': 'atual'},
]


def escrever_json(nome, obj):
    (SAIDA / nome).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def snapshot_prazo(base, dias_antes):
    """Consulta D-N, ou na aprovação se o prazo original for menor que N dias."""
    b = base.copy()
    agora = pd.concat([b.order_approved_at, b.fim_prazo - pd.Timedelta(days=dias_antes)], axis=1).max(axis=1)
    despacho = b.order_delivered_carrier_date.where(b.order_delivered_carrier_date.ge(b.order_approved_at))
    conhecido = despacho.where(despacho.le(agora))
    b['instante'] = agora
    b['ja_entregue'] = b.order_delivered_customer_date.le(agora)
    b['elegivel'] = ~b.ja_entregue & ((b.fim_prazo - agora) >= pd.Timedelta(days=1))
    b['dias_decorridos'] = (agora - b.order_approved_at).dt.total_seconds() / 86400
    b['dias_restantes'] = (b.fim_prazo - agora).dt.total_seconds() / 86400
    b['fracao_prazo_consumida'] = b.dias_decorridos / ((b.fim_prazo-b.order_approved_at).dt.total_seconds()/86400)
    b['despachado'] = conhecido.notna().astype(int)
    b['dias_ate_despacho'] = (conhecido-b.order_approved_at).dt.total_seconds()/86400
    b['dias_em_transito'] = (agora-conhecido).dt.total_seconds()/86400
    b['dias_ate_limite_postagem'] = (b.limite_postagem-agora).dt.total_seconds()/86400
    b['postagem_fora_limite'] = ((conhecido-b.limite_postagem).dt.total_seconds()/86400).clip(lower=0)
    b['folga_transito_historico'] = b.dias_restantes - (b.hist_entrega_dias_media-b.hist_postagem_dias_media).clip(lower=0)
    assert (conhecido.isna() | conhecido.le(agora)).all()
    assert not (b.ja_entregue & b.atrasou.eq(1)).any()
    return b


def extras(b):
    b = b.copy()
    transito = (b.hist_entrega_dias_media-b.hist_postagem_dias_media).clip(lower=0)
    b['folga_transito_historico'] = b.dias_restantes-transito
    b['excesso_transito_historico'] = b.dias_em_transito-transito
    b['proporcao_transito_historico'] = b.dias_em_transito/transito.clip(lower=.5)
    b['distancia_por_dia_restante'] = b.distancia_max_vendedores_km/b.dias_restantes.clip(lower=1)
    b['mes_consulta'] = b.instante.dt.month
    b['dia_semana_consulta'] = b.instante.dt.dayofweek
    return b


def atualizar_historicos(b, pedidos, itens):
    """Somente pedidos ativos: a entrega do próprio pedido ainda não é conhecida."""
    q = b.loc[b.elegivel, ['order_id', 'instante', 'prazo_prometido_dias']].rename(columns={'instante': 'order_approved_at'})
    h, _ = criar_historicos(pedidos, itens, q)
    h = h.set_index('order_id')
    out = b.copy()
    ids = out.loc[out.elegivel, 'order_id']
    out.loc[out.elegivel, COLUNAS_HISTORICO] = h.loc[ids, COLUNAS_HISTORICO].to_numpy()
    ultimo = h.loc[ids, 'ultimo_evento_historico'].reset_index(drop=True)
    agora = out.loc[out.elegivel, 'instante'].reset_index(drop=True)
    assert (ultimo.isna() | ultimo.lt(agora)).all()
    assert out.loc[out.elegivel, 'order_delivered_customer_date'].gt(agora.to_numpy()).all()
    return extras(out)


def fila_limitada(scores, elegiveis, ids, fracao=FRACAO, limiar=-np.inf):
    """Capacidade sobre TODOS os pedidos; empate resolvido por ID, nunca pelo alvo."""
    scores, elegiveis, ids = np.asarray(scores), np.asarray(elegiveis, bool), np.asarray(ids).astype(str)
    if not 0 <= fracao <= 1 or not (len(scores) == len(elegiveis) == len(ids)):
        raise ValueError('Capacidade ou tamanhos inválidos.')
    if not np.isfinite(scores[elegiveis]).all() or len(set(ids)) != len(ids):
        raise ValueError('Scores inválidos ou pedidos duplicados.')
    permitidos = np.flatnonzero(elegiveis & (scores >= limiar))
    ordem = np.lexsort((ids[permitidos], -scores[permitidos]))
    alertas = np.zeros(len(scores), dtype=int)
    alertas[permitidos[ordem[:int(len(scores)*fracao)]]] = 1
    return alertas


def metricas(b, scores, limiar=-np.inf, fracao=FRACAO):
    y = b.atrasou.to_numpy(int)
    alertas = fila_limitada(scores, b.elegivel, b.order_id, fracao, limiar)
    tn, fp, fn, tp = confusion_matrix(y, alertas, labels=[0, 1]).ravel()
    lead = (b.fim_prazo-b.instante).dt.total_seconds().to_numpy()/86400
    positives = (alertas == 1) & (y == 1)
    assert not (alertas.astype(bool) & ~b.elegivel.to_numpy()).any()
    assert np.all(lead[alertas == 1] >= 1)
    return {
        'pedidos': len(y), 'atrasos': int(y.sum()), 'pendentes_elegiveis': int(b.elegivel.sum()),
        'revisoes': int(alertas.sum()), 'fracao_revisao': float(alertas.mean()),
        'fracao_pendentes_revisada': float(alertas.sum()/max(1, b.elegivel.sum())),
        'recall': recall_score(y, alertas, zero_division=0),
        'precision': precision_score(y, alertas, zero_division=0),
        'f1': f1_score(y, alertas, zero_division=0), 'accuracy': accuracy_score(y, alertas),
        'AP': average_precision_score(y, scores),
        'tp': int(tp), 'fp': int(fp), 'fn': int(fn), 'tn': int(tn),
        'antecedencia_tp_mediana_dias': float(np.median(lead[positives])) if positives.any() else None,
        'antecedencia_alerta_minima_dias': float(np.min(lead[alertas == 1])) if alertas.any() else None,
    }, alertas


def corte_validacao(b, scores):
    """Melhor recall sob o teto, depois menor fila, na validação somente."""
    selecionados = fila_limitada(scores, b.elegivel, b.order_id)
    positivos = (selecionados == 1) & b.atrasou.eq(1).to_numpy()
    return float(np.min(np.asarray(scores)[positivos])) if positivos.any() else float('inf')


def preparar():
    if (SAIDA/'protocolo.json').exists():
        raise FileExistsError('Rodada existente: preserve os resultados antes de iniciar outra.')
    SAIDA.mkdir(parents=True, exist_ok=True)
    fontes = [RAIZ/'Modelos/experimento_operacional/snapshots.joblib',
              PASTA_ORIGINAIS/'olist_orders_dataset.csv', PASTA_ORIGINAIS/'olist_order_items_dataset.csv']
    protegidos = ['Modelos/rede_neural.pt', 'Modelos/preprocessador_rede.joblib',
                  'Modelos/dados_ml.joblib', 'Dados/pedidos_ml.csv']
    escrever_json('protocolo.json', {
        'data': '2026-11-16', 'objetivo': 'Maximizar recall com revisão <=30% de TODOS os pedidos',
        'fracao': FRACAO, 'configuracoes': CONFIGS, 'etapas_ml': ETAPAS_ML, 'checagens_regras': ETAPAS_REGRAS,
        'selecao': 'Maior recall na validação sob teto30%; depois menor fila e maior AP. Congelar antes do teste.',
        'teste': 'Maio-agosto/2018. Já consultado historicamente; avaliação retrospectiva, não teste inédito.',
        'politica': 'Corte de validação + limite top30 na coorte avaliada; ranking retrospectivo, não scheduler online.',
        'historicos': 'Desfechos e eventos estritamente anteriores à consulta; atualizações online sem retreino por rótulo futuro.',
        'hipoteses': ['Promessa estável e disponível desde aprovação.', 'Atualização de status sem latência.',
                     'Apenas coorte real de entregues elegíveis já existente; cancelados não representados.',
                     'D-N reavalia depois da aprovação; desempenho não equivale à previsão na aprovação.',
                     'Denominador inclui já entregues; proporção dos pendentes revisados também será reportada.'],
        'sha256_fontes': {str(p.relative_to(RAIZ)): sha256(p) for p in fontes},
        'sha256_aplicacao': {p: sha256(RAIZ/p) for p in protegidos},
        'codigo_sha256': sha256(Path(__file__)),
        'referencia_catboost': 'https://catboost.ai/docs/en/concepts/python-reference_catboostclassifier',
    })
    congelados = joblib.load(fontes[0])
    base = congelados['despacho']
    assert base.order_id.is_unique and base.origem_dado.eq('real_olist').all() and base.rotulo_observado.all()
    assert base.groupby('particao').size().to_dict() == {'treino': 41173, 'validacao': 24667, 'teste': 25550}
    alvo = base.order_delivered_customer_date.dt.normalize().gt(base.order_estimated_delivery_date.dt.normalize()).astype(int)
    assert alvo.eq(base.atrasou).all()
    for etapa in ETAPAS_REGRAS:
        congelados[etapa] = snapshot_prazo(base, int(etapa[1:]))
    pedidos = pd.read_csv(fontes[1])
    itens = pd.read_csv(fontes[2], usecols=['order_id', 'seller_id'])
    atuais = {}
    for etapa in ETAPAS_ML:
        print('HISTORICOS', etapa, flush=True)
        atuais[etapa] = atualizar_historicos(congelados[etapa], pedidos, itens)
    # As checagens usadas só como regras não precisam dos atributos históricos novos.
    congelados = {k: extras(v) for k, v in congelados.items()}
    joblib.dump({'congelados': congelados, 'atuais': atuais}, SAIDA/'dados_experimento.joblib', compress=3)
    print('PREPARADO: dados reais e históricos conferidos.', flush=True)


def colunas(etapa):
    return list(COLUNAS_PREVISAO) + COLUNAS_HISTORICO + ([] if etapa == 'aprovacao' else OPERACIONAIS+NOVAS)


def preprocessador(cols):
    nums = [c for c in cols if c not in CATEGORICAS]
    return ColumnTransformer([
        ('numericas', Pipeline([('nulos', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)),
                               ('escala', StandardScaler())]), nums),
        ('categoricas', Pipeline([('nulos', SimpleImputer(strategy='constant', fill_value='NAO_INFORMADO')),
                                 ('onehot', OneHotEncoder(handle_unknown='ignore', sparse_output=False))]), CATEGORICAS),
    ])


def dados_cat(b, cols):
    x = b[cols].copy()
    for c in CATEGORICAS:
        x[c] = x[c].fillna('NAO_INFORMADO').astype(str)
    for c in set(cols)-set(CATEGORICAS):
        x[c] = pd.to_numeric(x[c], errors='coerce').astype(float)
    return x


def treinar(b, etapa, config, treino, validacao, corte_treino):
    """Nenhum acesso a rótulos/scores do teste nesta função."""
    from catboost import CatBoostClassifier
    cols = colunas(etapa)
    t = b.loc[treino].copy()
    if config.get('janela'):
        t = t.loc[t.order_approved_at.ge(pd.Timestamp(corte_treino)-pd.Timedelta(days=config['janela']))]
    t = t.loc[t.elegivel]
    v = b.loc[validacao]
    ativos = v.elegivel.to_numpy()
    assert len(t) > 500 and t.atrasou.nunique() == 2
    assert t.rotulo_disponivel_em.lt(corte_treino).all()
    assert t.instante.lt(corte_treino).all()
    pre = None
    if config['tipo'] == 'CAT':
        xt, xv = dados_cat(t, cols), dados_cat(v.loc[v.elegivel], cols)
        modelo = CatBoostClassifier(iterations=400, depth=config['profundidade'], learning_rate=.05,
            l2_leaf_reg=8, loss_function='Logloss', random_seed=42, thread_count=4,
            allow_writing_files=False, verbose=False, cat_features=CATEGORICAS)
        modelo.fit(xt, t.atrasou.to_numpy(int))
        melhor = (-1., -1.)
        melhor_n = 400
        for n, ps in enumerate(modelo.staged_predict_proba(xv, eval_period=25), 1):
            s = np.full(len(v), -1.)
            s[ativos] = ps[:, 1]
            m, _ = metricas(v, s)
            chave = (m['recall'], m['AP'])
            if chave > melhor:
                melhor, melhor_n = chave, min(n*25, 400)
        modelo.shrink(melhor_n)
        pacote = {'tipo': 'CAT', 'modelo': modelo, 'colunas': cols, 'n_treino': len(t), 'iteracoes': melhor_n}
    else:
        pre = preprocessador(cols)
        xt = pre.fit_transform(t[cols]).astype(np.float32)
        xv = pre.transform(v.loc[v.elegivel, cols]).astype(np.float32)
        y = t.atrasou.to_numpy(int)
        if config['tipo'] == 'LR':
            modelo = LogisticRegression(C=config['C'], solver='liblinear', max_iter=1500, random_state=42,
                class_weight='balanced' if config.get('balanceado') else None)
            modelo.fit(xt, y)
        elif config['tipo'] == 'HGB':
            modelo = HistGradientBoostingClassifier(max_iter=250, learning_rate=.05,
                max_leaf_nodes=config['folhas'], min_samples_leaf=config['min_folha'],
                l2_regularization=5, early_stopping=False, random_state=42)
            modelo.fit(xt, y)
        else:
            import torch
            from torch import nn
            from torch.utils.data import DataLoader, TensorDataset
            torch.set_num_threads(2)
            redes, epocas = [], []
            x_t, x_v = torch.from_numpy(xt), torch.from_numpy(xv)
            y_t = torch.tensor(y, dtype=torch.float32)
            for seed in [7, 42]:
                torch.manual_seed(seed)
                rede = nn.Sequential(nn.Linear(xt.shape[1], 64), nn.ReLU(), nn.Dropout(.2),
                                     nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1))
                peso = float(np.sqrt((y == 0).sum()/max(1, (y == 1).sum())))
                loss = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([peso]))
                opt = torch.optim.AdamW(rede.parameters(), lr=.0003, weight_decay=.03)
                loader = DataLoader(TensorDataset(x_t, y_t), batch_size=512, shuffle=True,
                    generator=torch.Generator().manual_seed(seed))
                melhor, paciencia, melhor_estado, melhor_epoca = (-1., -1.), 0, None, 0
                for epoca in range(1, 81):
                    rede.train()
                    for a, target in loader:
                        opt.zero_grad()
                        erro = loss(rede(a).squeeze(1), target)
                        erro.backward()
                        opt.step()
                    rede.eval()
                    with torch.no_grad():
                        s = np.full(len(v), -1.)
                        s[ativos] = torch.sigmoid(rede(x_v).squeeze(1)).numpy()
                    m, _ = metricas(v, s)
                    chave = (m['recall'], m['AP'])
                    if chave > melhor:
                        melhor, paciencia = chave, 0
                        melhor_estado, melhor_epoca = deepcopy(rede.state_dict()), epoca
                    else:
                        paciencia += 1
                    if paciencia >= 12:
                        break
                redes.append(melhor_estado)
                epocas.append(melhor_epoca)
            modelo = {'estados': redes, 'entradas': xt.shape[1], 'epocas': epocas}
        pacote = {'tipo': config['tipo'], 'modelo': modelo, 'preprocessador': pre, 'colunas': cols, 'n_treino': len(t)}
    return pacote


def pontuar(pacote, b):
    s = np.full(len(b), -1.)
    ativos = b.elegivel.to_numpy()
    if not ativos.any():
        return s
    if pacote['tipo'] == 'CAT':
        x = dados_cat(b.loc[b.elegivel], pacote['colunas'])
        s[ativos] = pacote['modelo'].predict_proba(x)[:, 1]
    else:
        x = pacote['preprocessador'].transform(b.loc[b.elegivel, pacote['colunas']]).astype(np.float32)
        if pacote['tipo'] != 'MLP':
            s[ativos] = pacote['modelo'].predict_proba(x)[:, 1]
        else:
            import torch
            from torch import nn
            probs = []
            for estado in pacote['modelo']['estados']:
                rede = nn.Sequential(nn.Linear(x.shape[1], 64), nn.ReLU(), nn.Dropout(.2),
                                     nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1))
                rede.load_state_dict(estado)
                rede.eval()
                with torch.no_grad():
                    probs.append(torch.sigmoid(rede(torch.from_numpy(x)).squeeze(1)).numpy())
            s[ativos] = np.mean(probs, axis=0)
    return s


def desenvolver():
    if (SAIDA/'selecao_antes_teste.json').exists():
        raise FileExistsError('A seleção já foi congelada. Não retunar depois do teste nesta rodada.')
    dados = joblib.load(SAIDA/'dados_experimento.joblib')
    resultados, selecao, scores_salvos = [], {}, []
    for etapa in ETAPAS_ML:
        melhor = (-1., -np.inf, -1.)
        for config in CONFIGS:
            b = dados['atuais' if config['historico'] == 'atual' else 'congelados'][etapa]
            t = b.index[b.particao.eq('treino')]
            va = b.index[b.particao.eq('validacao')]
            assert b.loc[va, 'rotulo_disponivel_em'].lt('2018-05-01').all()
            inicio = time.perf_counter()
            print('TREINANDO', etapa, config['nome'], flush=True)
            pacote = treinar(b, etapa, config, t, va, '2018-01-01')
            s = pontuar(pacote, b.loc[va])
            corte = corte_validacao(b.loc[va], s)
            m, _ = metricas(b.loc[va], s, corte)
            top, _ = metricas(b.loc[va], s)
            resultados.append({'etapa': etapa, 'config': config['nome'], 'corte': corte,
                'n_treino': pacote['n_treino'], 'segundos': time.perf_counter()-inicio,
                'recall_top30': top['recall'], **m})
            p = b.loc[va, ['order_id', 'atrasou', 'instante']].copy()
            p['etapa'], p['config'], p['score'] = etapa, config['nome'], s
            scores_salvos.append(p)
            chave = (m['recall'], -m['revisoes'], m['AP'])
            if chave > melhor:
                melhor = chave
                selecao[etapa] = {'config': config, 'corte': corte, 'metricas_validacao': m}
                joblib.dump(pacote, SAIDA/f'modelo_{etapa}.joblib', compress=3)
            pd.DataFrame(resultados).to_csv(SAIDA/'comparacao_validacao.csv', index=False)
            print(f"RESULTADO {etapa}/{config['nome']}: recall {m['recall']:.4f}; fila {m['fracao_revisao']:.4f}; AP {m['AP']:.4f}", flush=True)
    # Regras completamente independentes de treinamento e dos rótulos.
    regras = []
    for etapa in ETAPAS_REGRAS:
        b = dados['congelados'][etapa]
        b = b.loc[b.particao.eq('validacao')]
        # Pendência e falta de postagem são conhecidas; empate por ID apenas.
        for regra in ['todos_pendentes', 'tempo_transito']:
            score = np.where(b.elegivel, 1., -1.) if regra == 'todos_pendentes' else np.where(b.elegivel, b.dias_decorridos.fillna(0)+b.dias_em_transito.fillna(0), -1.)
            m, _ = metricas(b, score)
            regras.append({'etapa': etapa, 'config': regra, **m})
    pd.DataFrame(regras).to_csv(SAIDA/'regras_validacao.csv', index=False)
    pd.concat(scores_salvos, ignore_index=True).to_parquet(SAIDA/'scores_validacao.parquet', index=False)
    escrever_json('selecao_antes_teste.json', {'momento': datetime.now().isoformat(),
        'selecao_por_etapa': selecao, 'regras': ETAPAS_REGRAS,
        'aviso': 'Seleção encerrada; próximos resultados não alteram configuração.'})
    print('SELECAO CONGELADA', {k: v['config']['nome'] for k, v in selecao.items()}, flush=True)


def avaliar():
    if (SAIDA/'metricas_teste.csv').exists():
        raise FileExistsError('Avaliação já registrada; não sobrescrever.')
    dados = joblib.load(SAIDA/'dados_experimento.joblib')
    selecao = json.loads((SAIDA/'selecao_antes_teste.json').read_text(encoding='utf-8'))
    cv, resultados, previsoes, mensais = [], [], [], []
    # Estabilidade: configurações já escolhidas, dobras por tempo com rótulo maduro.
    for etapa, escolhido in selecao['selecao_por_etapa'].items():
        cfg = escolhido['config']
        b = dados['atuais' if cfg['historico'] == 'atual' else 'congelados'][etapa]
        desenvolvimento = b.loc[b.particao.eq('treino')]
        for dobra, (c, e, f) in enumerate(DOBRAS, 1):
            print('DOBRA', etapa, dobra, flush=True)
            t, va, a = divisao_temporal(desenvolvimento, c, e, f)
            pacote = treinar(b, etapa, cfg, t, va, c)
            corte = corte_validacao(b.loc[va], pontuar(pacote, b.loc[va]))
            m, _ = metricas(b.loc[a], pontuar(pacote, b.loc[a]), corte)
            cv.append({'etapa': etapa, 'dobra': dobra, 'config': cfg['nome'], **m})
            pd.DataFrame(cv).to_csv(SAIDA/'validacao_temporal.csv', index=False)
    # Teste retrospectivo: cada política foi congelada antes desta leitura de scores.
    for etapa, escolhido in selecao['selecao_por_etapa'].items():
        cfg = escolhido['config']
        b = dados['atuais' if cfg['historico'] == 'atual' else 'congelados'][etapa]
        b = b.loc[b.particao.eq('teste')]
        pacote = joblib.load(SAIDA/f'modelo_{etapa}.joblib')
        s = pontuar(pacote, b)
        for politica, corte in [('corte_validacao_cap30', escolhido['corte']), ('ranking_top30', -np.inf)]:
            m, alerta = metricas(b, s, corte)
            resultados.append({'etapa': etapa, 'modelo': cfg['nome'], 'politica': politica, **m})
            p = b[['order_id', 'atrasou', 'instante', 'fim_prazo', 'elegivel', 'order_approved_at']].copy()
            p['score'], p['alerta'], p['etapa'], p['politica'] = s, alerta, etapa, politica
            previsoes.append(p)
            for mes, ix in b.groupby(b.order_approved_at.dt.strftime('%Y-%m')).groups.items():
                pos = b.index.get_indexer(ix)
                # Reproduz as decisões globais, sem recalibrar no mês.
                yy, aa = b.loc[ix, 'atrasou'].to_numpy(int), alerta[pos]
                mensais.append({'etapa': etapa, 'politica': politica, 'mes_aprovacao': mes,
                    'pedidos': len(ix), 'atrasos': int(yy.sum()), 'revisoes': int(aa.sum()),
                    'recall': recall_score(yy, aa, zero_division=0), 'fracao_revisao': aa.mean()})
    for etapa in ETAPAS_REGRAS:
        b = dados['congelados'][etapa]
        b = b.loc[b.particao.eq('teste')]
        for regra in ['todos_pendentes', 'tempo_transito']:
            s = np.where(b.elegivel, 1., -1.) if regra == 'todos_pendentes' else np.where(b.elegivel, b.dias_decorridos.fillna(0)+b.dias_em_transito.fillna(0), -1.)
            m, alerta = metricas(b, s)
            resultados.append({'etapa': etapa, 'modelo': regra, 'politica': 'regra_cap30', **m})
            p = b[['order_id', 'atrasou', 'instante', 'fim_prazo', 'elegivel', 'order_approved_at']].copy()
            p['score'], p['alerta'], p['etapa'], p['politica'] = s, alerta, etapa, regra
            previsoes.append(p)
    pd.DataFrame(resultados).to_csv(SAIDA/'metricas_teste.csv', index=False)
    pd.DataFrame(mensais).to_csv(SAIDA/'metricas_mensais.csv', index=False)
    pd.concat(previsoes, ignore_index=True).to_parquet(SAIDA/'previsoes_teste.parquet', index=False)
    protocolo = json.loads((SAIDA/'protocolo.json').read_text(encoding='utf-8'))
    assert all(sha256(RAIZ/p) == h for p, h in protocolo['sha256_aplicacao'].items())
    print(pd.DataFrame(resultados)[['etapa', 'modelo', 'politica', 'recall', 'precision', 'accuracy', 'fracao_revisao']].to_string(index=False), flush=True)
    print('CONCLUIDO: aplicação e dados originais preservados.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fase', choices=['preparar', 'treinar', 'avaliar'], required=True)
    args = parser.parse_args()
    # Compatibilidade Pandas 2.3/NumPy 2.5; não oculta erros nem alertas de convergência.
    warnings.filterwarnings('ignore', message="The 'generic' unit for NumPy timedelta is deprecated", category=DeprecationWarning)
    with threadpool_limits(limits=4):
        {'preparar': preparar, 'treinar': desenvolver, 'avaliar': avaliar}[args.fase]()


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
