# EntregaCerta AI

Projeto do Bootcamp TCS que combina dados reais da Olist, PostgreSQL, Machine Learning
e GenAI para apoiar a revisão humana de pedidos com risco de atraso. Interface em
HTML, CSS e JavaScript, com backend FastAPI e áreas de cliente e analista.

**Modelo ativo:** CatBoost VER0.7, no checkpoint D−1 da entrega prometida.
A demonstração usa 18.603 pedidos históricos e seleciona, por dia, 65% dos
pedidos ainda pendentes, arredondando para cima. Resultados e limitações no
[registro da integração](Docs/modelo_ver07.md).

## Abrir o sistema

Abra o Docker Desktop. No terminal do VS Code, **na pasta deste README**, execute:

```powershell
.\.venv\Scripts\python.exe main.py iniciar
```

Acesse **http://127.0.0.1:8000**. Mantenha o terminal aberto; Ctrl+C encerra a API.
Esse comando inicia o PostgreSQL e a aplicação usando o modelo salvo. Não é preciso
executar notebooks, treinar modelos ou abrir arquivos de src individualmente.

**Primeira instalação:** siga o [guia completo](Docs/instalacao_novo_pc.md),
com dependências, dados, PostgreSQL, login e abertura da aplicação.
O CatBoost recebido já está treinado; não é necessário executar o notebook 02.
O [fluxo anterior da MLP](Docs/versoes/instalacao_mlp_pre_ver07.md) continua disponível.

**Para estudar e treinar a VER0.7:** abra
[03_catboost_ver07.ipynb](Notebooks/03_catboost_ver07.ipynb). Ele reúne tratamento,
EDA, treino e avaliação em ordem; use o kernel de pesquisa e **Executar tudo**.
Os resultados ficam em `Modelos/notebook_ver07/`, separados do modelo ativo.

## Comandos principais

Todos usam .\.venv\Scripts\python.exe main.py seguido da ação:

| Ação | Quando usar |
|---|---|
| iniciar | Abrir o sistema no dia a dia |
| preparar-banco | Primeira instalação ou atualização das tabelas web; preserva histórico já carregado |
| preparar-catboost | Carregar as entradas verificadas da VER0.7 em schema próprio |
| criar-analista --nome "Nome" --email "email@equipe.com" | Cadastrar analista; a senha é solicitada no terminal |
| verificar | Conferir conexão, consulta e inferência reais, sem alterar os dados |
| testar --postgres | Testar aplicação e integração com banco em schemas temporários |
| --help | Consultar a ajuda |

## Onde trabalhar

```text
main.py           Comandos para operar o projeto
src/              Backend, PostgreSQL, dados, inferência e GenAI
frontend/         Páginas, estilos, scripts e logo em uso
Dados/            CSVs originais e bases preparadas
Modelos/          catboost_ver07: modelo ativo, entradas e auditoria; MLP preservada
Notebooks/        01/02: preparação e treinamento do fluxo anterior da MLP
  03_catboost_ver07.ipynb  Tratamento, EDA e treino didático do CatBoost
  experimentos/   CTGAN, históricos e comparações opcionais
  legado/         Estudos antigos preservados para consulta
experimentos/     Scripts, configurações e testes da pesquisa de ML
  EntregaCertaAI_VER07/  Pacote recebido, preservado integralmente
tests/            Testes da aplicação e da preparação de dados
Docs/             Guia, histórico técnico, proveniência, registros e BPMN
```

Para continuar a pesquisa, consulte o [índice dos experimentos](experimentos/README.md).
Os resultados, limitações e etapas realizadas estão no [registro técnico](Docs/desenvolvimento.md).
Os JSONs de experimentos/configuracoes controlam o cenário CTGAN; os arquivos
em Modelos registram modelos, parâmetros e resultados, por isso foram preservados.

## Limites atuais

No assistente, clique nas referências **[E6]** ou em **Ver evidências** para conferir
os campos, a fonte e a data da consulta usados naquela resposta. Valores ausentes
aparecem como “Não informado”. A lista é produzida pelo servidor; ela não garante
que toda interpretação da IA esteja correta. Perguntas gerais mostram os dados
disponibilizados sem atribuir a eles uma citação que a resposta não fez.

Os [testes de qualidade da GenAI](Docs/avaliacao_genai_2026-11-16.csv) registram
respostas reais do Gemini a cenários fictícios, alertas automáticos e leitura
assistida. Há instruções de reprodução e limitações no [registro técnico](Docs/desenvolvimento.md).

- O dashboard faz reprodução histórica; o score é risco relativo, não probabilidade calibrada.
- A loja é demonstrativa, sem pagamento ou envio real. Seus novos pedidos ainda não recebem score de ML.
- A fila CatBoost usa 65% dos pendentes de cada dia em D−1. No teste histórico, foram 990 alertas: 5,32% de todos os pedidos, mas 66,62% dos pendentes, devido ao arredondamento diário.
- O teste da VER0.7 foi reutilizado e a cota escolhida após exploração. As métricas são exploratórias; não demonstram generalização. A MLP anterior mantém sua política original quando selecionada.
- O assistente exige configuração de API. Se ela falhar, consultas, ML e relatório com resumo automático continuam disponíveis.
- Métricas de cenários sintéticos não comprovam desempenho em atrasos reais.

## Pontos de restauração

**Ponto 0:** estado anterior à organização. **Ponto 1:** versão organizada.
As cópias locais ficam na pasta irmã EntregaCertaAI_Pontos, fora do repositório.
Veja a seção de restauração no [guia anterior](Docs/versoes/instalacao_mlp_pre_ver07.md). Dados e modelos
foram preservados; ambientes Python e volume PostgreSQL ficam fora dos ZIPs.

**Antes do CatBoost:** `Docs/versoes/antes_catboost_ver07_20261117.zip` guarda
o código e a documentação existentes antes desta integração. Modelos MLP e
tabelas históricas permanecem nos locais originais. [Como alternar](Docs/modelo_ver07.md#voltar-à-mlp).

Fonte: Brazilian E-Commerce Public Dataset by Olist, registros anonimizados de
2016–2018. Proveniência e condições de uso em [recebimento_dataset.txt](Docs/recebimento_dataset.txt).
