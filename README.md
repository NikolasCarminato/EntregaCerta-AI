# EntregaCerta AI

Projeto do Bootcamp TCS que combina dados reais da Olist, PostgreSQL, Machine Learning
e GenAI para apoiar a revisão humana de pedidos com risco de atraso. Interface em
HTML, CSS e JavaScript, com backend FastAPI e áreas de cliente e analista.

**Modelo ativo:** CatBoost VER0.7, no checkpoint D−1 da entrega prometida.
A demonstração usa 18.603 pedidos históricos e seleciona, por dia, 65% dos
pedidos ainda pendentes, arredondando para cima. Resultados e limitações no
[registro da integração](Docs/modelo_ver07.md).

## Acesso rápido

| Preciso de... | Onde abrir |
|---|---|
| Slides atuais (V17, 26 slides) | [PowerPoint](output/apresentacao/EntregaCerta_AI_apresentacao_v17.pptx) |
| Falas das quatro pessoas e tempo por slide | [Roteiro PDF V17](output/apresentacao/EntregaCerta_AI_roteiro_4_pessoas_v17.pdf) · [TXT editável](Docs/roteiro_apresentacao.txt) |
| Fala individual da Pessoa 4 | [Roteiro PDF V17](output/apresentacao/EntregaCerta_AI_roteiro_pessoa_4_v17.pdf) |
| Levar os materiais da apresentação | [Pacote completo V17](output/apresentacao/EntregaCerta_AI_v17_completo.zip) |
| Instalar ou abrir a aplicação | [Guia de inicialização](Docs/instalacao_novo_pc.md) |
| Preparar as respostas para a banca | [Perguntas e respostas](Docs/perguntas_respostas_banca.txt) |
| Consultar apresentações e roteiros antigos | [Versões anteriores compactadas](output/versoes/entregaveis_anteriores_20261119.zip) |

O roteiro planeja **19min40s de apresentação e 20s de margem**, incluindo a
demonstração de 3 minutos. Extrair o pacote completo antes de abrir o PowerPoint;
manter o TXT do prompt e o PDF de exemplo junto do PPTX para preservar os links.

## Diagramas do projeto

Revisados em **18/11/2026**, com base no código atual e no catálogo PostgreSQL.

| Diagrama | Visualizar | Editar |
|---|---|---|
| Processo BPMN: fila D−1, loja e assistente | [PNG](Docs/diagramas/processo.png) · [SVG](Docs/diagramas/processo.svg) | [BPMN 2.0](Docs/diagramas/processo.bpmn) |
| DER: tabelas, chaves e relações | [PNG](Docs/diagramas/der.png) · [SVG](Docs/diagramas/der.svg) | [draw.io](Docs/diagramas/der.drawio) |
| Arquitetura da aplicação | [PNG](Docs/diagramas/arquitetura.png) · [SVG](Docs/diagramas/arquitetura.svg) | [draw.io](Docs/diagramas/arquitetura.drawio) |

Use os PNGs na apresentação e os SVGs para ampliar sem perder definição.
Os arquivos `.drawio` abrem no diagrams.net/draw.io; o `.bpmn`, em um editor
compatível com BPMN 2.0. O artigo foi retirado dos entregáveis por decisão da
equipe em 18/11/2026. O registro técnico e a apresentação continuam no escopo.

## Abrir o sistema

**Opção com Terraform + Docker local:** com Docker Desktop aberto e Terraform
instalado, execute na raiz:

```powershell
.\.venv\Scripts\python.exe infra/local/gerenciar.py iniciar
```

Acesse **http://localhost:8001**. O banco existente é reutilizado, com seu volume
preservado. Nas próximas aberturas, acrescente `--sem-build` para reutilizar a
imagem. Use `parar` no lugar de `iniciar` para desligar apenas a aplicação.
Veja os [pré-requisitos, proteção do banco e comandos de conferência](Docs/instalacao_novo_pc.md#opção-com-terraform-e-docker-local).

**Opção direta com Python:**

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

As orientações para desenvolvimento com IA ficam em
[.agents/skills/](.agents/skills/entregacerta-vibe-code/SKILL.md).
São quatro skills: desenvolvimento geral, frontend, GenAI e backend/infraestrutura.
Elas mantêm o contexto atual do projeto e indicam como implementar e conferir
mudanças. Cada pasta contém um SKILL.md e metadados de identificação.

Exemplo de pedido: **“Use $entregacerta-frontend para melhorar os filtros de
Pedidos, preservando a intro e o ML, e confira o fluxo.”** Para outras tarefas,
use $entregacerta-vibe-code, $entregacerta-genai ou $entregacerta-backend-infra.
Se não aparecerem na lista do assistente, indique o caminho do SKILL.md
correspondente no pedido.

```text
main.py           Comandos para operar o projeto
Dockerfile        Imagem da aplicação, com dependências de execução
infra/local/      Terraform, dependências e comando de operação do Docker local
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
Docs/             Guias, registro técnico, proveniência e roteiro editável
  diagramas/      BPMN, DER e arquitetura: arquivos editáveis e imagens
  evidencias/genai/  CSVs das avaliações e PDFs dos testes técnicos
  versoes/        Guias e código anteriores à integração CatBoost
output/
  apresentacao/   V17, roteiro atual, anexos e pacote para levar à banca
  relatorios/     PDFs recentes exportados pela aplicação
  versoes/       Apresentações e roteiros anteriores em um único ZIP
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

Os [testes de qualidade da GenAI](Docs/evidencias/genai/avaliacao_genai_2026-11-16.csv) registram
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

**Organização de novembro/2026:** temporários e versões anteriores foram
copiados e conferidos por SHA-256 antes da limpeza. O backup dos arquivos
reorganizados está em `../EntregaCertaAI_Pontos/organizacao_20261119_v15/`, fora
do Git. Os caminhos de execução, dados, modelos e infraestrutura foram mantidos.
Esse backup da limpeza não substitui o volume PostgreSQL nem o backup do projeto.

Fonte: Brazilian E-Commerce Public Dataset by Olist, registros anonimizados de
2016–2018. Proveniência e condições de uso em [recebimento_dataset.txt](Docs/recebimento_dataset.txt).
