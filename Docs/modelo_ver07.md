# CatBoost VER0.7 — integração de 17/11/2026

Modelo recebido pela equipe em `EntregaCertaAI_VER07_Experimento.zip`, integrado
na branch `rnn-train`. A reprodução carrega os modelos treinados e reconstrói
suas entradas; **nenhum modelo foi retreinado** nesta integração.

## Modelo e momento da previsão

Modelo ativo: `pilot_Dminus1_CB07_closed_only.cbm`, escolhido no pacote entre
as variantes do piloto usando a validação. São 111 atributos, com categorias
nativas do CatBoost. A cópia usada pelo backend está em
`Modelos/catboost_ver07/modelo.cbm`.

O checkpoint é **00:00 do dia anterior à entrega prometida (D−1)**. Só pedidos
elegíveis ainda pendentes nesse instante recebem score. Em cada dia, a fila
seleciona `ceil(0,65 × pendentes do dia)`, ordenados pelo score; empates seguem
a ordem original do experimento. Não é uma previsão no instante da compra.

Pedidos entregues antes do checkpoint não são avaliados pelo CatBoost. Recebem
zero convencional no contrato numérico e a indicação “Não pontuado” na lista;
isso não representa probabilidade zero de atraso. O score dos pendentes também
não é uma probabilidade calibrada.

## Resultados efetivamente reproduzidos

Coorte original: 96.470 pedidos entregues elegíveis, 6.534 atrasos. A divisão
cronológica original tem 64.320 / 13.547 / 18.603 pedidos. O filtro de maturidade
dos rótulos deixa **12.175 pedidos** na validação/calibração efetiva.

| Medida | Validação efetiva | Teste histórico |
|---|---:|---:|
| Pedidos totais | 12.175 | 18.603 |
| Pendentes em D−1 | 1.017 | 1.486 |
| Atrasos reais | 674 | 672 |
| Alertas | 680 | 990 |
| Atrasos encontrados (TP) | 480 | 553 |
| Falsos positivos (FP) | 200 | 437 |
| Atrasos não encontrados (FN) | 194 | 119 |
| Verdadeiros negativos (TN) | 11.301 | 17.494 |
| Precision | 70,59% | 55,86% |
| Recall | 71,22% | 82,29% |
| F1 | 70,90% | 66,55% |
| Average Precision | 0,7553 | 0,7303 |
| Accuracy, incluindo já entregues | 96,76% | 97,01% |
| Alertas / todos os pedidos | 5,59% | 5,32% |
| Alertas / pendentes | 66,86% | 66,62% |

A cota é diária: arredondar para cima explica por que o percentual agregado
supera 65%. Os 97,01% de accuracy incluem 17.117 pedidos já entregues antes
do checkpoint; não são evidência isolada da qualidade do classificador.
AP foi calculada sobre o score, não sobre a classificação binária.

Os três `.cbm` reproduziram os scores recebidos, com diferença absoluta máxima
de **1,11 × 10⁻¹⁶**. Os alertas do piloto escolhido foram iguais aos recebidos.
Também foram conferidos `pilot_Dminus1_CB07_with_mql.cbm` (114 atributos) e
`Dminus1_winner_CB06.cbm` (95). O vencedor D−2 em `.txt` é LightGBM e permanece
apenas no pacote; não é o modelo da aplicação.

## Fluxo do sistema

```text
ZIP Olist + distâncias + fontes preparadas do pacote
  → experimentos/preparar_ver07.py
  → atributos D−1 e auditoria separada
  → main.py preparar-catboost
  → PostgreSQL: modelo_ver07.pedidos
  → src/catboost_ver07.py: predict_proba do .cbm + cota diária
  → FastAPI → HTML/CSS/JS, relatório PDF e contexto do assistente
```

O PostgreSQL armazena os 111 atributos e metadados conhecidos no checkpoint
em JSONB, com versão e chave única. **Não armazena alvos nem scores nessa tabela.**
A API calcula as previsões carregando o `.cbm`; não consulta o CSV de auditoria.
O schema `public`, a view antiga e as contas em `app_web` foram preservados.

Os pedidos criados pela loja demonstrativa continuam separados. Para pontuar
pedidos operacionais novos será necessário reconstruir os mesmos 111 atributos
com dados disponíveis em D−1. A integração atual é uma reprodução histórica.

## Arquivos e dependências

- `experimentos/EntregaCertaAI_VER07/`: 56 arquivos originais extraídos, intactos.
- `experimentos/preparar_ver07.py`: reconstrução das entradas e verificação dos modelos.
- `Modelos/catboost_ver07/config.json`: contrato, categorias, política e hashes.
- `Modelos/catboost_ver07/pedidos_d1.parquet`: entradas sem desfechos, 30.778 pedidos.
- `Modelos/catboost_ver07/auditoria_previsoes.csv`: rótulos e resultados para auditoria offline.
- `Modelos/catboost_ver07/reproducao.json`: métricas, coberturas, hashes e versões executadas.
- `src/catboost_ver07.py`: carga SQL isolada, inferência e política diária.
- `tests/test_catboost_ver07.py`: regras, reprodução real e integração HTTP/SQL/PDF.
- `Docs/versoes/`: snapshot do código anterior e guias antigos.

A aplicação usa `catboost==1.2.10`, acrescentado a `requirements.txt`.
A reprodução tem ambiente separado, via `requirements-ver07.txt`, incluindo
XGBoost: o código recebido o importa, mas sua lista de dependências o omitia.
A execução realizada usou o ambiente de pesquisa `.venv-ctgan`, com Pandas
2.3.3; a API usa `.venv`, com Pandas 3.1.0. Não houve uso de CTGAN nesta etapa.
O passo a passo para criar um ambiente de reprodução novo está no
[guia de instalação](instalacao_novo_pc.md).

## Limitações para o artigo e a banca

- O teste foi reutilizado na pesquisa e a cota de 65% foi escolhida após
  exploração. São resultados **exploratórios**, não uma avaliação final intocada.
- D−1 oferece mais informação, mas reduz o tempo disponível para intervenção.
  Não compare diretamente com a MLP na aprovação: coorte, momento e fila mudaram.
- A regra escolhida pela validação (`days_after_shipping_limit`) obteve F1 de
  56,56% no teste. Outra regra (`not_dispatched_then_age`) chegou a 66,43% no
  diagnóstico exploratório de teste, perto dos 66,55% do piloto, mas não foi
  selecionada pela validação. Não confundir esses dois comparadores nem afirmar
  superioridade estatística sobre todas as regras simples.
- A matriz rodoviária é de maio de 2024, posterior aos pedidos de 2016–2018,
  e cobre cerca de 15,70% dos pedidos. É uma aproximação temporal importante.
- O marketing com contrato anterior cobre cerca de 4,57% dos pedidos.
  O clima é agregado por UF; a disponibilidade usa atraso declarado ou a
  hipótese de sete dias. Os CSVs preparados de clima foram utilizados como
  recebidos, sem reauditoria de toda a origem bruta do INMET nesta integração.
- Históricos usam entregas anteriores ao checkpoint. Os rótulos observados
  servem à auditoria; campos de entrega futura não entram na API de inferência.
- A coorte contém pedidos entregues; não valida cancelamentos, outras lojas,
  períodos atuais, efeito de ações humanas ou ROI. Não foram gerados dados
  sintéticos, resultados ou ganhos de negócio para esta integração.

## Integridade e restauração

SHA256 do ZIP recebido:
`f5001e5531ffc286d8de02b0dc17416aac9a73fe3f41cea52d1515aee1cfbd24`.
Hashes dos ZIPs de dados e dos três modelos estão em `reproducao.json`.
O script exige os hashes originais dos ZIPs para reproduzir essa versão.

Snapshot anterior: `Docs/versoes/antes_catboost_ver07_20261117.zip`.
Ele contém código, frontend e documentação locais anteriores à integração;
não contém `.env`, ambientes Python nem backup do banco. Os modelos antigos
permanecem em `Modelos/`, sem sobrescrita. Para restaurar código, examine o ZIP
em outra pasta e compare os arquivos; não extraia cegamente sobre trabalho novo.

### Voltar à MLP

Defina `ENTREGACERTA_MODELO=mlp` no `.env` e reinicie o servidor. A aplicação
voltará ao preprocessador, pesos, view e validação antigos, com a fila original.
Use `ENTREGACERTA_MODELO=catboost_ver07` para retornar à VER0.7.
`ENTREGACERTA_PARTICAO=teste` é o padrão do CatBoost; `validacao` seleciona
os 12.175 pedidos da validação efetiva. Alterações exigem reinício para limpar
os caches do modelo e da consulta.


## Conferências da integração

- `main.py testar --postgres`: 95 testes aprovados, incluindo o backend real
  conectado ao PostgreSQL e a reprodução dos scores/alertas.
- Após mudança paralela do assistente, `test_*genai.py`: 38 testes aprovados,
  com sobreposição à suíte anterior; inclui a regra de cotas diárias.
- `main.py verificar`: 30.778 entradas no banco, 18.603 no lote, 990 na fila.
- Carga repetida sem duplicação; `public.pedidos` manteve 91.390 registros.
- MLP anterior executada: 24.667 pedidos e 2.466 alertas; artefatos com mesmos hashes.
- JavaScript: sintaxe e validação do payload real conferidas; cota inválida rejeitada.
- PDF: duas páginas e texto/indicadores conferidos. Sem validação visual no browser.
- Provedor GenAI simulado nos testes; não houve chamada real ao Gemini nesta tarefa.


## Notebook didático de tratamento, EDA e treino

[03_catboost_ver07.ipynb](../Notebooks/03_catboost_ver07.ipynb) contém a sequência
explicada e executável de ponta a ponta: 64 células, 31 de código e nove figuras.
A execução final em kernel limpo reproduziu exatamente os scores do piloto
recebido, com diferença máxima zero, e os 990 alertas. Os resultados ficam em
`Modelos/notebook_ver07/<execucao>/`, separados da aplicação. A promoção do
novo artefato não é automática. Os notebooks anteriores permanecem intactos.

Selecione o kernel de pesquisa `.venv-ctgan` neste computador (sem uso de GAN)
ou `.venv-ver07` preparado com `requirements-ver07.txt`; confira os caminhos dos
ZIPs na seção 1.1 e use **Reiniciar Kernel → Executar Tudo**. Não precisa
executar o notebook 02 ou scripts de preparação antes dele.
