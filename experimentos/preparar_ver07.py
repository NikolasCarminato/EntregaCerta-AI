"""Reproduz os .cbm recebidos e prepara entradas históricas, sem retreinar.

O pacote original fica intacto em EntregaCertaAI_VER07. O backend recebe
atributos congelados em D-1; alvos ficam apenas no arquivo de auditoria.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import accuracy_score, average_precision_score

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'experimentos' / 'EntregaCertaAI_VER07'
sys.path.insert(0, str(PACKAGE / 'src'))
import benchmark_ver07 as v
import olist_checkpoint as ck
import ver04_features as vf
import external_features as ef
import benchmark_ver06 as b06
from pilot_daily_budget_ver07 import add_mql_feature


def digest(path):
    return v.sha256(path)


def metrics(y, scores, alerts, pending):
    result = v.metric(y, alerts)
    result.update(accuracy=float(accuracy_score(y, alerts)),
                  average_precision_all=float(average_precision_score(y, scores)),
                  average_precision_pending=float(average_precision_score(y[pending], scores[pending])),
                  pending=int(pending.sum()),
                  fraction_alerts_all=float(alerts.mean()),
                  fraction_alerts_pending=float(alerts.sum() / pending.sum()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--olist', type=Path, required=True)
    parser.add_argument('--roads', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'Modelos' / 'catboost_ver07')
    args = parser.parse_args()
    sources = json.loads((PACKAGE / 'manifesto_arquivos.json').read_text(encoding='utf-8'))['original_sha256']
    hashes = {}
    for name, path in [('logistic-brazilian-dataset.zip', args.olist), ('dist_brasil.zip', args.roads)]:
        print('Conferindo fonte:', name, flush=True)
        hashes[name] = digest(path)
        if hashes[name] != sources[name]:
            raise ValueError(f'SHA256 diferente do manifesto: {name}. Reprodução interrompida.')
    print('Reconstruindo pedidos e checkpoint D-1...', flush=True)
    base = ck.build_base(args.olist).sort_values(['order_purchase_timestamp', 'order_id']).reset_index(drop=True)
    if len(base) != 96470 or base.late.sum() != 6534 or not base.order_id.is_unique:
        raise ValueError('Coorte diferente do pacote original.')
    x = ck.build_checkpoint(base, days_before=1)
    X0, _ = ck.feature_table(x)
    X = vf.enhanced_features(x, X0)
    ext = PACKAGE / 'inputs_preparados'
    coverage = {}
    for name, builder in [
        ('marketing', lambda: ef.add_marketing(base, ext / 'olist_closed_deals_dataset.csv')),
        ('roads', lambda: ef.add_roads(base, args.roads, ext / 'municipios_ibge.csv')),
        ('weather', lambda: ef.add_weather(x, ext / 'weather_daily.csv')),
    ]:
        print('Reconstituindo atributos:', name, flush=True)
        frame, coverage[name] = builder()
        if not frame.order_id.equals(base.order_id):
            raise ValueError('Desalinhamento de pedidos no enriquecimento.')
        X = pd.concat([X, frame.drop(columns='order_id')], axis=1)
    X = b06.clean_categorical(X)
    features = v.add_practical_features(x, X)
    split = np.array(['train'] * 64320 + ['validation'] * 13547 + ['test'] * 18603)
    idx = b06.make_train_cal_test_indices(x, split)
    y = x.late.to_numpy(dtype=int)
    model = CatBoostClassifier()
    selected_name = 'pilot_Dminus1_CB07_closed_only.cbm'
    model.load_model(str(PACKAGE / 'results' / selected_name))
    if list(features.columns) != model.feature_names_:
        raise ValueError('Ordem ou nomes dos atributos diferentes do modelo salvo.')
    scores = np.zeros(len(x))
    active = idx['pending']
    scores[active] = model.predict_proba(features.loc[active])[:, 1]
    audit = {'models': {}, 'metrics': {}, 'sources_sha256': hashes, 'coverage': coverage,
             'test_reused': True, 'quota_post_hoc': True, 'training_executed': False,
             'runtime_versions': {n: importlib.metadata.version(n) for n in
                                  ['numpy', 'pandas', 'catboost', 'scikit-learn']}}
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    original = json.loads((PACKAGE / 'results' / 'VER07_pilot_operational.json').read_text())
    if original['selected'] != 'CB07_closed_only':
        raise ValueError('Vencedor inesperado no pacote.')
    references = []
    for part, key, reference_key in [('validacao', 'cal', 'selected_validation'), ('teste', 'test', 'selected_test')]:
        ids = idx[key]
        alerts = v.top_ratio_policy(scores[ids], active[ids], x.checkpoint_at.iloc[ids], .65)
        actual = metrics(y[ids], scores[ids], alerts, active[ids])
        for metric in ('n', 'positives', 'tp', 'fp', 'fn', 'tn', 'alerts', 'precision', 'recall', 'f1'):
            if not np.isclose(actual[metric], original[reference_key][metric], rtol=0, atol=1e-12):
                raise ValueError(f'Métrica não reproduzida: {part}/{metric}')
        audit['metrics'][part] = actual
        refs = pd.DataFrame({'order_id': x.order_id.iloc[ids].to_numpy(), 'particao': part,
                             'checkpoint_at': x.checkpoint_at.iloc[ids].to_numpy(),
                             'atrasou': y[ids], 'pendente': active[ids],
                             'score_atraso': scores[ids], 'prioridade': alerts})
        references.append(refs)
        if part == 'teste':
            saved = pd.read_csv(PACKAGE / 'results' / 'pilot_CB07_closed_only_test_predictions.csv')
            if not np.array_equal(refs.order_id, saved.order_id):
                raise ValueError('Pedidos do teste não correspondem ao arquivo recebido.')
            error = float(np.max(np.abs(scores[ids] - saved.score.to_numpy())))
            if error > 1e-9 or not np.array_equal(alerts, saved.alert):
                raise ValueError('Scores ou prioridades diferentes do pacote recebido.')
            audit['models'][selected_name] = {'max_score_diff': error, 'alerts_equal': True,
                                              'features': len(model.feature_names_), 'sha256': digest(PACKAGE/'results'/selected_name)}
        print(part, actual, flush=True)
    # Verifica também os outros .cbm preservados, sem selecionar outro modelo.
    mql, _ = add_mql_feature(base, ext/'olist_closed_deals_dataset.csv', ext/'olist_marketing_qualified_leads_dataset.csv')
    for filename, frame, csv_name in [
        ('pilot_Dminus1_CB07_with_mql.cbm', b06.clean_categorical(pd.concat([features, mql], axis=1)), 'pilot_CB07_with_mql_test_predictions.csv'),
        ('Dminus1_winner_CB06.cbm', X, 'Dminus1_winner_predictions.csv'),
    ]:
        other = CatBoostClassifier()
        other.load_model(str(PACKAGE/'results'/filename))
        saved = pd.read_csv(PACKAGE/'results'/csv_name)
        if list(frame.columns) != other.feature_names_ or not np.array_equal(saved.order_id, x.order_id.iloc[idx['test']]):
            raise ValueError('Contrato diferente: ' + filename)
        predicted = other.predict_proba(frame.iloc[idx['testpending']])[:, 1]
        error = float(np.max(np.abs(predicted - saved.loc[saved.pending.eq(1), 'score'].to_numpy())))
        if error > 1e-9:
            raise ValueError('Modelo não reproduzido: ' + filename)
        audit['models'][filename] = {'max_score_diff': error, 'features': len(other.feature_names_), 'sha256': digest(PACKAGE/'results'/filename)}
    ids = np.concatenate([idx['cal'], idx['test']])
    meta = x.iloc[ids][['order_id', 'checkpoint_at', 'order_purchase_timestamp', 'order_estimated_delivery_date']].copy()
    meta['ordem_original'] = ids
    meta['particao'] = ['validacao'] * len(idx['cal']) + ['teste'] * len(idx['test'])
    meta['pendente'] = active[ids]
    meta['elegivel'] = x.eligible.iloc[ids].to_numpy()
    meta['order_approved_at'] = x.order_approved_at.iloc[ids].where(x.approval_known.iloc[ids])
    mapping = {'customer_state':'customer_state','uf_origem':'seller_state','categoria_principal':'category',
               'quantidade_itens':'n_items','quantidade_vendedores':'n_sellers','valor_itens':'subtotal',
               'valor_frete':'freight_total','valor_pago':'payment_total','peso_total_g':'weight_total',
               'distancia_vendedor_referencia_km':'haversine_km','prazo_prometido_dias':'promised_days',
               'tipo_pagamento_principal':'payment_type','parcelas_maximas':'installments_max'}
    # Nomes de display que já existem entre as features compartilham a mesma coluna.
    frame = features.iloc[ids].copy()
    for name, value in meta.items():
        frame[name] = value
    for name, source in mapping.items():
        frame[name] = x[source].iloc[ids]
    if any(c in frame for c in ['late', 'atrasou', 'order_delivered_customer_date']):
        raise ValueError('Alvo ou entrega futura no arquivo de inferência.')
    frame.reset_index(drop=True).to_parquet(out/'pedidos_d1.parquet', index=False)
    pd.concat(references, ignore_index=True).to_csv(out/'auditoria_previsoes.csv', index=False)
    shutil.copy2(PACKAGE/'results'/selected_name, out/'modelo.cbm')
    config = {'tipo':'catboost_ver07','versao':'VER0.7 piloto','modelo':'modelo.cbm',
              'model_sha256':digest(out/'modelo.cbm'), 'dados_sha256':digest(out/'pedidos_d1.parquet'),
              'colunas_entrada':model.feature_names_,
              'colunas_categoricas':[model.feature_names_[i] for i in model.get_cat_feature_indices()],
              'fracao_fila':.65, 'politica':'cota_diaria_pendentes', 'checkpoint':'D-1 às 00:00',
              'particao_padrao':'teste', 'dados':'pedidos_d1.parquet', 'test_reused':True,
              'quota_post_hoc':True, 'calibration':'não calibrado', 'coorte_original':96470,
              'contagens':{p:audit['metrics'][p]['n'] for p in audit['metrics']}}
    for file, data in [('config.json',config),('reproducao.json',audit)]:
        (out/file).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Reprodução concluída. Artefatos:', out, flush=True)


if __name__ == '__main__':
    main()
