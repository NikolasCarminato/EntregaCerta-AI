#!/usr/bin/env python3
"""Generate human-readable and machine-auditable VER0.7 documentation and chart."""
from pathlib import Path
import json,hashlib,platform,sys,zipfile,shutil
import pandas as pd,numpy as np
from datetime import datetime,timezone
from matplotlib import pyplot as plt
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'results';D=ROOT/'docs';D.mkdir(exist_ok=True)

p=json.loads((R/'VER07_pilot_operational.json').read_text());exp=json.loads((R/'VER07_complete_results.json').read_text());heur=json.loads((R/'VER07_heuristic_comparison.json').read_text());audit=json.loads((R/'VER07_reproduction_audit.json').read_text())
best=next(z for z in p['variants'] if z['variant']==p['selected']);miss=next(z for z in p['variants'] if z['variant']!=p['selected'])
d1=exp['details']['D-1'];d2=exp['details']['D-2'];s=best['test'];v=best['val'];r=best['rule'];bl=best['paired_block_bootstrap']

def percent(x):return f'{x*100:.2f}'.replace('.',',')+'%'

sha={}
for f in ['logistic-brazilian-dataset.zip','2016.zip','2017.zip','2018.zip','dist_brasil.zip','archive.zip']:
 path=Path('/mnt/data')/f
 if path.exists():
  with open(path,'rb') as fi: sha[f]=hashlib.file_digest(fi,'sha256').hexdigest()
# Source manifests of local prepared CSV are included; original uploaded ZIPs remain untouched.
prepared=ROOT/'inputs_preparados';prepared.mkdir(exist_ok=True)
for f in ['olist_closed_deals_dataset.csv','olist_marketing_qualified_leads_dataset.csv','weather_daily.csv','municipios_ibge.csv']:
 src=Path('/mnt/data/VER06_reais/data')/f
 dst=prepared/f
 shutil.copy2(src,dst)

metrics=[
('Regra: todos pendentes D−1',r['precision'],r['recall'],r['f1']),
('VER0.3 D−1 (referência)',.4792,.9241,.6311),
('VER0.6 D−1 (selecionado)',.4537,.9836,.6209),
('VER0.7 validação: CB06 / limiar',d1['winner_test']['precision'],d1['winner_test']['recall'],d1['winner_test']['f1']),
('VER0.7 piloto: CB07 / top 65% D−1',s['precision'],s['recall'],s['f1'])]
fig,ax=plt.subplots(figsize=(12,5.1))
names=[x[0] for x in metrics];vals=[100*x[3] for x in metrics]
bars=ax.barh(names,vals)
for bar,x in zip(bars,vals):ax.text(x+.2,bar.get_y()+bar.get_height()/2,f'{x:.2f}%',va='center',fontsize=10)
ax.invert_yaxis();ax.set_xlim(0,77);ax.set_xlabel('F1-score (%)');ax.set_title('EntregaCerta AI — D−1: referências e piloto VER0.7');
fig.text(.02,.01,'Piloto 65% definido após exploração do mesmo teste; não é uma validação independente.',fontsize=9)
fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(R/'comparacao_F1_VER07.png',dpi=150,bbox_inches='tight');plt.close(fig)

intro=f'''# EntregaCerta AI — VER0.7 (experimento exploratório)

**Data:** 17/11/2026 | **Status:** piloto experimental com metas atingidas em período reutilizado, **não homologado**.  
**Arquivos originais:** lidos em modo somente leitura, sem alterações. **Semente:** 42.

## 1. Resultado que merece atenção

A estratégia operacional **D−1 com CatBoost e limite diário de 65% de pedidos pendentes** alcançou no conjunto já utilizado anteriormente:

| Métrica | Valor | Meta |
| --- | ---: | ---: |
| Precision | **{percent(s['precision'])}** | ≥55% |
| Recall | **{percent(s['recall'])}** | ≥80% |
| F1 | **{percent(s['f1'])}** | ≥65% |
| Acertos (TP) | {s['tp']} | — |
| Alertas falsos (FP) | {s['fp']} | — |
| Atrasos não alertados (FN) | {s['fn']} | — |
| Acertos sem atraso (TN) | {s['tn']} | — |
| Pedidos avaliados | {s['n']} | — |

**Atenção: os 65% foram explorados após observar métricas desse mesmo período histórico.** O algoritmo e o ranking são reais, mas o desempenho acima **não prova generalização**. É essencial validar em um período cego para a banca e produção.

Na validação cronologicamente anterior, a mesma política já fixada alcançou Precision {percent(v['precision'])}, Recall {percent(v['recall'])} e F1 {percent(v['f1'])}. O modelo escolhido entre **closed deals** e **closed deals + leads (MQL)** foi o primeiro, por F1 da validação ({percent(v['f1'])} contra {percent(miss['val']['f1'])}). Os leads foram realmente testados, mas não melhoraram o F1 da validação.

## 2. Por que o ganho veio de um controle operacional?

Em D−1, todos os pedidos que atrasariam ainda estão pendentes; alertar todos produz Recall 100% e F1 {percent(r['f1'])}, mas também muitos alertas falsos. O limite de 65% por dia exige um **ranking de risco**: para cada data de checkpoint, o sistema pontua os pedidos pendentes às 00:00 e alerta `ceil(0.65 × número_de_pendentes_do_dia)`. Não precisa conhecer rótulos futuros para ordenar os pedidos do dia.

Comparando o piloto com a regra de alertar todos, o F1 subiu **{100*(s['f1']-r['f1']):.2f} pontos percentuais**, com bootstrap em blocos de dia descritivo [{100*bl['ci95_lower']:.2f}; {100*bl['ci95_upper']:.2f}] p.p. O bootstrap **não corrige** a escolha exploratória de 65% no teste reaproveitado.

**Comparação honesta com regras simples:** uma heurística sem ML, priorizando pedidos ainda não despachados e depois os mais antigos, também atingiu F1 de 66,43% nesse período, quase igual aos 66,55% do CatBoost. Essa heurística **não foi escolhida pela validação** (lá venceu outra regra que teve F1 de 56,56% no teste); ainda assim, o teste exploratório não comprova vantagem relevante do ML sobre todas as regras plausíveis.

## 3. Rastreabilidade dos dados e tratamentos

**Olist:** 96.470 entregas concluídas com datas válidas; 6.534 atrasos. Alvo `dia_entrega_real > dia_prometido`. Junção de tabelas por `order_id`, `seller_id`, `customer_id`; itens/pagamentos agregados por pedido; produtos e CEPs agregados; nulos tratados como `NaN` para numéricos e `__MISSING__` para categorias. Limpeza de geolocalizações fora dos limites plausíveis do Brasil.

**INMET 2016–2018 (dados reais):** CSVs horários convertidos para dados diários por UF, com limpeza de `-9999`, data e decimal brasileiro, mínimo de 18 horas válidas por estação/dia e agregação por médias de estações. Janelas de chuva/temperatura de sete dias só utilizam observações cuja data de disponibilidade `available_at` não é posterior ao checkpoint. O lag de publicação **+7 dias é uma hipótese conservadora**, não carimbo real de publicação. Clima estadual é proxy, não previsão futura de chuva nem condição da rota.

**Matriz rodoviária Zenodo (dados reais):** `dist_brasil.zip`, códigos IBGE direcionais origem→destino, distância em km e duração em minutos. Correspondência UF+cidade com somente **83 municípios verificados**, gerando cerca de **15,70% de cobertura das rotas**; pares ausentes ficam `NaN`. A rede é de maio de 2024; aproximá-la a pedidos de 2016–2018 é uma limitação temporal. Distância em linha reta Haversine do Olist continua disponível.

**Marketing Funnel Olist (dados reais):** negócios fechados relacionados a `seller_id`, somente quando `won_date <= data_compra` e contatos de leads apenas quando anteriores ao fechamento. Cobertura pré-compra de cerca de **4,57%**. A ablação incluiu `origin`, `landing_page_id` e idade do contato, mas foi preterida pela validação.

**Calendário:** dia da semana, mês, prazo prometido, feriados fixos e datas móveis. Carnaval/Corpus Christi são observâncias segundo código, não obrigatoriamente feriados nacionais em todas as localidades.

**Novas features na VER0.7:** médias históricas de atraso e tempo de transporte por vendedor e por rota, calculadas estritamente com entregas concluídas antes de cada checkpoint; razão entre distância rodoviária e Haversine, tempo rodoviário, velocidade implícita, prazo consumido e tempo desde despacho (somente após despacho conhecido). Nenhuma data real de entrega futura ou evento futuro foi usada diretamente como variável preditora.

## 4. Metodologia e limites

- **Divisão cronológica pela data da compra:** treino 64.320 pedidos (5.113 atrasos), validação 13.547 (749 atrasos), teste 18.603 (672 atrasos). O conjunto de teste já havia sido usado em VER0.1–VER0.6.
- **Elegibilidade:** pedido comprado antes do checkpoint; apenas pedidos pendentes recebem risco; os já concluídos não geram alerta.
- **Proteção temporal:** exemplos de treino precisam ter entregas concluídas antes da primeira data de validação; a calibração usa apenas resultados encerrados antes do começo do teste. Esse filtro reduziu o treino D−1 para 5.655 pedidos pendentes e mudou o perfil das classes, exigindo cautela.
- **Modelos e experimentos:** quatro classificadores principais (três CatBoost e um LightGBM) com histórico/geo/clima/calendário, seleção de threshold ou limites diários por validação; refino D−1 com dois CatBoost (com e sem MQL). Não foi executada nova busca Optuna nesta rodada; parâmetros partiram de testes anteriores e hipóteses predefinidas. Não foi usado PCA nem SMOTE.
- **Seleção formal pela validação:** em D−2, LightGBM + limiar, F1 **{percent(d2['winner_test']['f1'])}**; em D−1, CatBoost com limiar, F1 **{percent(d1['winner_test']['f1'])}**. Esses vencedores *não* superaram os recordes anteriores no mesmo checkpoint.
- **Piloto 65%:** escolhido após explorar o teste reutilizado; apesar de reproduzível, é **post hoc** e precisa ser validado em dados novos. O resultado de 66,55% não substitui automaticamente os modelos validados das versões anteriores.
- **Métrica:** Precision = TP/(TP+FP); Recall = TP/(TP+FN); F1 = 2×Precision×Recall/(Precision+Recall). Na operação, o custo de falsos alertas e o tempo de antecedência também importam.

## 5. Como rodar e conferir

Consulte `docs/COMO_REPRODUZIR_NO_CODEX.md`. Os scripts usam somente os dados originais enviados e os CSVs preparados incluídos no pacote. Os modelos `.cbm`, previsões completas e parâmetros salvos estão em `results/`.

**Auditoria:** {audit['n_prediction_rows']} previsões salvas, {audit['n_model_scored_pending']} pedidos realmente pontuados, diferença máxima entre modelo recarregado e pontuações do CSV de **{audit['max_probability_abs_diff']:.3g}**, alertas reconstruídos idênticos, hash do Olist preservado.

## 6. Próximo passo indispensável

Congelar código, conjunto de features, hiperparâmetros e política operacional; executar em dados novos não consultados, com avaliação temporal real de rastreamento e pedidos não concluídos. Se o modelo não superar uma regra simples de prioridades nesse conjunto, usar a regra simples como baseline de produção e o ML somente como experimento.
'''
(D/'README_VER07.md').write_text(intro,encoding='utf8')

(D/'GUIA_BANCA_VER07.md').write_text(f'''# Guia didático para a banca — VER0.7

**Qual é o problema?** Antecipar quais encomendas do e-commerce podem chegar depois da data prometida.

**O que entra na IA?** Dados internos da compra, produto, frete, geografia, histórico de vendedores e rotas, clima histórico observado e já disponível, feriados, perfil comercial que já existia na compra e status de despacho até a data da consulta.

**O que não entra?** Data de entrega futura, chuva futura, resultado de outros pedidos que ainda não foram concluídos ou cadastro comercial posterior à compra.

**Por que CatBoost?** Modelo de árvores adequado a dados tabulares com muitas variáveis numéricas e categóricas; foi comparado com LightGBM nesta rodada. Não presumimos que mais parâmetros sempre melhoram.

**Por que D−1?** As informações de despacho e tempo já transcorrido são mais úteis perto do prazo; em troca, resta menos tempo para agir. D−2 permanece na arquitetura como alerta antecipado.

**Por que limitar os alertas a 65% dos pedidos pendentes?** Um limite operacional obriga o sistema a priorizar o risco. Sem limite, a regra de alertar todos já alcança Recall 100%, mas com excesso de alarmes falsos. Em D−1, a política de 65% alcançou {percent(s['recall'])} Recall, {percent(s['precision'])} Precision e {percent(s['f1'])} F1 no teste exploratório.

**O modelo foi realmente melhor que uma regra simples?** Ganhou {100*(s['f1']-r['f1']):.2f} p.p. de F1 da regra de alertar *todos*; entretanto, uma regra simples alternativa com o mesmo orçamento chegou a F1 66,43% (CatBoost: 66,55%), então a superioridade do ML sobre essa alternativa não foi demonstrada.

**Por que a VER0.7 ainda é experimental?** O limite de 65% foi explorado no mesmo teste de 2018 visto nas versões anteriores. Isso cria risco de otimização ao teste. Para aprovação definitiva, precisamos congelar tudo e testar em dados verdadeiramente inéditos.

**Como tratamos os dados externos?** INMET → médias diárias por estado e janela defasada, sem chuva futura; Zenodo → códigos IBGE e pares dirigidos com 15,70% de cobertura e rede 2024; Marketing Funnel → junção pelo vendedor apenas para negócios fechados antes da compra.

**Por que não usaram PCA?** CatBoost não precisa de redução linear por padrão; o custo-benefício maior veio do contexto logístico e da estratégia de alertas.

**O que significa cada métrica?** Precision: chance de um alerta emitido estar correto. Recall: proporção de atrasos reais encontrados. F1: equilíbrio harmônico entre as duas. Acima de 65% de F1 **não** equivale a 65% de pedidos corretos no total.

**Próximos riscos:** dados antigos 2016–2018, mudança de taxa de atrasos ao longo do tempo, mapeamento rodoviário parcial, clima estadual defasado e ausência de rastreamento logístico granular real.
''',encoding='utf8')

(D/'COMO_REPRODUZIR_NO_CODEX.md').write_text('''# Rodar VER0.7 no Codex / ambiente Python

1. Descompacte o pacote VER0.7 em uma pasta de trabalho. Copie para `entradas/` os dois ZIPs **sem modificar**: `logistic-brazilian-dataset.zip` e `dist_brasil.zip`. Os demais ZIPs (2016, 2017, 2018, archive) servem como fontes de proveniência; os CSVs derivados necessários já acompanham este pacote em `inputs_preparados/`.
2. Crie um ambiente virtual e instale `pip install -r requirements_ver07.txt`.
3. Execute na raiz descompactada:

```bash
python src/benchmark_ver07.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao --days 2 1
python src/pilot_daily_budget_ver07.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao
python src/verify_saved_model.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao
```

`benchmark_ver07.py`: compara 4 famílias/variações de modelos e políticas, seleciona por validação. `pilot_daily_budget_ver07.py`: experimento adicional com orçamento operacional diário de 65% (pós-hoc) e duas ablações de Marketing Funnel. `verify_saved_model.py`: recarrega o modelo treinado e verifica sua reprodutibilidade.

**Avisos:** a função `build_base` e os CSVs derivados foram trazidos da VER0.6 e estão em `src/support`; o mapeamento IBGE ainda é parcial (83 municípios). Os dados brutos INMET só precisam ser reproces-sados caso a equipe queira mudar as regras de agregação; para isso, reaproveite `prepare_inmet_weather.py` da VER0.6 com os ZIPs originais.

**Validação de reprodutibilidade:** comparar `results/VER07_pilot_operational.json` com `resultados_execucao/VER07_pilot_operational.json`, além dos hashes em `manifesto_arquivos.json`. Resultados em CPU diferentes podem ter pequenas diferenças numéricas, que não devem alterar as conclusões.

**Para uma banca:** apresentar as métricas como *experimento exploratório com teste reutilizado*, não como teste cego independente. Repetir o experimento em dados novos antes de produção.
'''.replace('reproces-sados','reprocessados'),encoding='utf8')

(D/'HISTORICO_E_DECISOES.md').write_text(f'''# Histórico e decisões VER0.7

| Versão | Checkpoint | Precision | Recall | F1 | Seleção/escopo |
| --- | --- | ---: | ---: | ---: | --- |
| VER0.2 | D−2 | 47,80% | 80,80% | 60,07% | Otimização anterior, teste reutilizado |
| VER0.3 | D−1 | 47,92% | 92,41% | 63,11% | Referência anterior |
| VER0.6 reais | D−1 | 45,37% | 98,36% | 62,09% | Features externas reais, validação selecionou sem rodovias |
| VER0.7 seleção formal | D−1 | {percent(d1['winner_test']['precision'])} | {percent(d1['winner_test']['recall'])} | {percent(d1['winner_test']['f1'])} | Modelo/limiar escolhidos pela validação |
| **VER0.7 piloto** | **D−1** | **{percent(s['precision'])}** | **{percent(s['recall'])}** | **{percent(s['f1'])}** | **Cota 65% definida exploratoriamente depois de reutilizar o teste** |

Versões são preservadas sem sobrescrita. As diferentes estratégias usam o mesmo período de 2018 e não podem ser tratadas como evidências independentes.

## Heurísticas sem ML com a mesma cota de 65%/dia

| Regra | F1 validação | F1 teste |
| --- | ---: | ---: |
'''+ ''.join(f"| {v0['heuristic']} | {percent(v0['validation']['f1'])} | {percent(v0['test_diagnostic']['f1'])} |\n" for v0 in heur['candidates'])+f'''
**A seleção da heurística por validação** foi `{heur['validation_selected']}` com F1 teste {percent(heur['selected_test']['f1'])}. Outra heurística quase empatou com o CatBoost no teste, o que reforça a necessidade de prova independente.
''',encoding='utf8')

(D/'requirements_ver07.txt').write_text('''numpy>=1.24,<3
pandas>=2.0,<3
scikit-learn>=1.4,<2
catboost>=1.2,<2
lightgbm>=4,<5
optuna>=4,<5
python-dateutil>=2.8,<3
matplotlib>=3.8,<4
''',encoding='utf8')
shutil.copy2(D/'requirements_ver07.txt',ROOT/'requirements_ver07.txt')

manifest={'version':'VER0.7', 'created_utc':datetime.now(timezone.utc).isoformat(), 'original_sha256':sha,
 'saved_model_verified':audit, 'test_reused':True, 'policy_post_hoc':True,
 'sources_integrated':['Olist','INMET 2016-2018 processed daily UF','Zenodo road 2024 partial codes','Olist closed deals','Olist MQL ablation','calendar/holidays'],
 'source_coverage':p['sources'], 'models_selected_validation':{'D-2':d2['winner_selected_validation_only'],'D-1':d1['winner_selected_validation_only']},
 'pilot_selected_by_validation':p['selected'], 'pilot_metrics_test':s}
(ROOT/'manifesto_arquivos.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
print('Created docs, chart, manifest',ROOT,flush=True)
