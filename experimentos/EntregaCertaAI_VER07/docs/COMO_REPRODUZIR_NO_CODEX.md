# Rodar VER0.7 no Codex / ambiente Python

1. Descompacte o pacote VER0.7 em uma pasta de trabalho. Copie para `entradas/` os dois ZIPs **sem modificar**: `logistic-brazilian-dataset.zip` e `dist_brasil.zip`. Os demais ZIPs (2016, 2017, 2018, archive) servem como fontes de proveniência; os CSVs derivados necessários já acompanham este pacote em `inputs_preparados/`.
2. Crie um ambiente virtual e instale `pip install -r requirements_ver07.txt`.
3. Execute na raiz descompactada:

```bash
python src/benchmark_ver07.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao --days 2 1
python src/pilot_daily_budget_ver07.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao
python src/verify_saved_model.py --olist entradas/logistic-brazilian-dataset.zip --roads entradas/dist_brasil.zip --external inputs_preparados --out resultados_execucao
```

`benchmark_ver07.py`: compara 4 famílias/variações de modelos e políticas, seleciona por validação. `pilot_daily_budget_ver07.py`: experimento adicional com orçamento operacional diário de 65% (pós-hoc) e duas ablações de Marketing Funnel. `verify_saved_model.py`: recarrega o modelo treinado e verifica sua reprodutibilidade.

**Avisos:** a função `build_base` e os CSVs derivados foram trazidos da VER0.6 e estão em `src/support`; o mapeamento IBGE ainda é parcial (83 municípios). Os dados brutos INMET só precisam ser reprocessados caso a equipe queira mudar as regras de agregação; para isso, reaproveite `prepare_inmet_weather.py` da VER0.6 com os ZIPs originais.

**Validação de reprodutibilidade:** comparar `results/VER07_pilot_operational.json` com `resultados_execucao/VER07_pilot_operational.json`, além dos hashes em `manifesto_arquivos.json`. Resultados em CPU diferentes podem ter pequenas diferenças numéricas, que não devem alterar as conclusões.

**Para uma banca:** apresentar as métricas como *experimento exploratório com teste reutilizado*, não como teste cego independente. Repetir o experimento em dados novos antes de produção.
