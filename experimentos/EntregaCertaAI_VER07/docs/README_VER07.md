# EntregaCerta AI — VER0.7 (experimento exploratório)

**Data:** 17/11/2026 | **Status:** piloto experimental com metas atingidas em período reutilizado, **não homologado**.  
**Arquivos originais:** lidos em modo somente leitura, sem alterações. **Semente:** 42.

## 1. Resultado que merece atenção

A estratégia operacional **D−1 com CatBoost e limite diário de 65% de pedidos pendentes** alcançou no conjunto já utilizado anteriormente:

| Métrica | Valor | Meta |
| --- | ---: | ---: |
| Precision | **55,86%** | ≥55% |
| Recall | **82,29%** | ≥80% |
| F1 | **66,55%** | ≥65% |
| Acertos (TP) | 553 | — |
| Alertas falsos (FP) | 437 | — |
| Atrasos não alertados (FN) | 119 | — |
| Acertos sem atraso (TN) | 17494 | — |
| Pedidos avaliados | 18603 | — |

**Atenção: os 65% foram explorados após observar métricas desse mesmo período histórico.** O algoritmo e o ranking são reais, mas o desempenho acima **não prova generalização**. É essencial validar em um período cego para a banca e produção.

Na validação cronologicamente anterior, a mesma política já fixada alcançou Precision 70,59%, Recall 71,22% e F1 70,90%. O modelo escolhido entre **closed deals** e **closed deals + leads (MQL)** foi o primeiro, por F1 da validação (70,90% contra 69,87%). Os leads foram realmente testados, mas não melhoraram o F1 da validação.

## 2. Por que o ganho veio de um controle operacional?

Em D−1, todos os pedidos que atrasariam ainda estão pendentes; alertar todos produz Recall 100% e F1 62,28%, mas também muitos alertas falsos. O limite de 65% por dia exige um **ranking de risco**: para cada data de checkpoint, o sistema pontua os pedidos pendentes às 00:00 e alerta `ceil(0.65 × número_de_pendentes_do_dia)`. Não precisa conhecer rótulos futuros para ordenar os pedidos do dia.

Comparando o piloto com a regra de alertar todos, o F1 subiu **4.27 pontos percentuais**, com bootstrap em blocos de dia descritivo [1.78; 6.36] p.p. O bootstrap **não corrige** a escolha exploratória de 65% no teste reaproveitado.

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
- **Seleção formal pela validação:** em D−2, LightGBM + limiar, F1 **56,53%**; em D−1, CatBoost com limiar, F1 **62,40%**. Esses vencedores *não* superaram os recordes anteriores no mesmo checkpoint.
- **Piloto 65%:** escolhido após explorar o teste reutilizado; apesar de reproduzível, é **post hoc** e precisa ser validado em dados novos. O resultado de 66,55% não substitui automaticamente os modelos validados das versões anteriores.
- **Métrica:** Precision = TP/(TP+FP); Recall = TP/(TP+FN); F1 = 2×Precision×Recall/(Precision+Recall). Na operação, o custo de falsos alertas e o tempo de antecedência também importam.

## 5. Como rodar e conferir

Consulte `docs/COMO_REPRODUZIR_NO_CODEX.md`. Os scripts usam somente os dados originais enviados e os CSVs preparados incluídos no pacote. Os modelos `.cbm`, previsões completas e parâmetros salvos estão em `results/`.

**Auditoria:** 18603 previsões salvas, 1486 pedidos realmente pontuados, diferença máxima entre modelo recarregado e pontuações do CSV de **1.11e-16**, alertas reconstruídos idênticos, hash do Olist preservado.

## 6. Próximo passo indispensável

Congelar código, conjunto de features, hiperparâmetros e política operacional; executar em dados novos não consultados, com avaliação temporal real de rastreamento e pedidos não concluídos. Se o modelo não superar uma regra simples de prioridades nesse conjunto, usar a regra simples como baseline de produção e o ML somente como experimento.
