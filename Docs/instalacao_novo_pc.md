# Guia de inicialização — CatBoost VER0.7

Use o **PowerShell na raiz do repositório**, a pasta que contém `main.py`.
No VS Code: Terminal → Novo Terminal. Não execute os módulos de `src` isoladamente.

**Neste computador, a carga CatBoost já está pronta.** Encerre o servidor antigo
com Ctrl+C, abra o Docker Desktop e execute:

```powershell
.\.venv\Scripts\python.exe main.py iniciar
```

Acesse http://127.0.0.1:8000. As contas existentes continuam válidas.
O dashboard exige login de analista; o backend também restringe esse acesso.
A demonstração padrão agora é CatBoost D−1, com 18.603 pedidos históricos.

## Ordem para instalar do zero

| Ordem | Ação | Resultado |
|---|---|---|
| 1 | Criar `.venv` e instalar `requirements.txt` | Ambiente da aplicação |
| 2 | Disponibilizar CSVs Olist e artefatos CatBoost | Dados e modelo prontos |
| 3 | Criar `.env` a partir do exemplo | Conexão local e seleção do modelo |
| 4 | `main.py preparar-catboost` | PostgreSQL, entradas D−1 e tabelas web |
| 5 | `main.py criar-analista ...` | Conta de acesso, se ainda não existir |
| 6 | `main.py verificar` e `main.py testar --postgres` | Conferências reais |
| 7 | `main.py iniciar` | Aplicação web |

O CatBoost recebido **já está treinado**. Para abrir esta versão não execute
`src.preparar_dados`, `src.tratar_dados_ml` ou o notebook 02: eles pertencem ao
fluxo anterior da MLP. Seus arquivos e instruções estão preservados no
[guia anterior](versoes/instalacao_mlp_pre_ver07.md).

## 1. Dependências da aplicação

Instale Python 3.12, Git, VS Code e Docker Desktop. Se `.venv` ainda não existir:

```powershell
py -3.12 -m venv .venv
```

Instale ou atualize as dependências, sem recriar um ambiente existente:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

O CatBoost ativo usa versão 1.2.10. No VS Code, selecione o interpretador da
`.venv`. Os comandos deste guia usam seu executável diretamente; não é
necessário ativar o ambiente no terminal.

## 2. Dados e artefatos

Mantenha os nove CSVs Olist originais em `Dados/Brazilian Logistic/`.
A loja usa especialmente `olist_products_dataset.csv` e
`olist_order_items_dataset.csv`. A base é pública e anonimizada; veja
[proveniência e condições de uso](recebimento_dataset.txt).

A pasta `Modelos/catboost_ver07/` precisa conter:

- `modelo.cbm`: pesos recebidos, sem retreinamento nesta integração.
- `config.json`: atributos, categorias, versão, hashes e regra da fila.
- `pedidos_d1.parquet`: entradas reconstruídas para validação e teste.
- `auditoria_previsoes.csv` e `reproducao.json`: evidências offline para conferência.

**Se esses arquivos já estiverem presentes, avance para a etapa 3.**
A aplicação não utiliza o CSV de auditoria para gerar previsões.

### Reconstrução opcional a partir das fontes originais

Use esta sequência somente se os artefatos estiverem ausentes ou se quiser
reproduzir o experimento. Ela recompõe atributos e verifica os `.cbm`, sem treinar.
O pacote intacto deve estar em `experimentos/EntregaCertaAI_VER07/`.
É necessário possuir os ZIPs originais `logistic-brazilian-dataset.zip` e
`dist_brasil.zip`; o script confere seus hashes contra o manifesto recebido.
Os dados preparados de clima, IBGE e marketing acompanham o pacote.

Crie um ambiente separado para as dependências dos scripts recebidos:

```powershell
py -3.12 -m venv .venv-ver07
.\.venv-ver07\Scripts\python.exe -m pip install -r requirements-ver07.txt
.\.venv-ver07\Scripts\python.exe -m pip check
.\.venv-ver07\Scripts\python.exe -X utf8 experimentos/preparar_ver07.py --olist "C:\caminho\logistic-brazilian-dataset.zip" --roads "C:\caminho\dist_brasil.zip"
```

Substitua os dois caminhos pelos locais reais. `requirements-ver07.txt` completa
as dependências originais com XGBoost, que era importado mas não estava listado.
Uma reprodução anterior nesta máquina utilizou `.venv-ctgan`; esse nome é apenas
o ambiente disponível e não significa treinamento com GAN.

O destino padrão é `Modelos/catboost_ver07/`. Para investigar uma nova execução
sem sobrescrever os artefatos ativos, acrescente `--out Modelos/verificacao_ver07`.
Compare `reproducao.json` e os hashes antes de substituir qualquer versão.
Uma mudança nas entradas já carregadas no PostgreSQL é recusada pela carga,
para preservar a versão em uso. O treino original é uma pesquisa separada,
documentada no pacote; não é uma etapa obrigatória da instalação.

## 3. Configurar `.env`

Se não existir, copie `.env.example` para `.env`. Se já existir, preserve suas
senhas e chaves. Preencha `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` e `POSTGRES_DB` com os valores do banco. No Docker local,
a porta externa prevista é 5433. Não publique o `.env` no GitHub.

Selecione a versão:

```dotenv
ENTREGACERTA_MODELO=catboost_ver07
ENTREGACERTA_PARTICAO=teste
```

A partição `validacao` seleciona 12.175 pedidos; `teste`, 18.603. A regra da
fila é 65% dos **pendentes de cada dia em D−1**, arredondando para cima.
Ela não é 65% de toda a base. O score é relativo, não probabilidade calibrada.

Para usar o assistente, configure `GEMINI_API_KEY` e um `GEMINI_MODEL` disponível
para sua chave. Sem API de GenAI, consultas, ML e relatório básico continuam
funcionando. Não é necessário chamar a GenAI para validar o modelo.

## 4. Preparar PostgreSQL

Abra Docker Desktop e execute:

```powershell
.\.venv\Scripts\python.exe main.py preparar-catboost
```

Esse comando inicia o serviço PostgreSQL, confere o modelo e o Parquet,
carrega **30.778 entradas** no schema `modelo_ver07` e inicializa as tabelas
web quando necessário. A tabela contém atributos e contexto, sem alvos ou
scores. Tabelas anteriores, contas e pedidos da loja são preservados.
Repetir a mesma carga é permitido e não duplica registros. Uma carga diferente
é recusada, sem apagar a existente.

Se o PostgreSQL já estiver iniciado ou for externo, use `--sem-docker`.
`main.py preparar-banco` também encaminha para essa rotina quando CatBoost
está selecionado. Não execute manualmente o script antigo de carga da MLP.

## 5. Conta de analista

Se já tiver uma conta, mantenha-a. Para cadastrar outra:

```powershell
.\.venv\Scripts\python.exe main.py criar-analista --nome "Nome da pessoa" --email "pessoa@equipe.com"
```

A senha é solicitada no terminal, com confirmação. Não existe senha de analista
fixa neste guia. O cadastro público da loja cria clientes, sem acesso ao dashboard.

## 6. Validar

```powershell
.\.venv\Scripts\python.exe main.py verificar
.\.venv\Scripts\python.exe main.py testar --postgres
```

Na configuração padrão, o diagnóstico deve informar **30.778 registros no
banco do modelo, 18.603 no lote e 990 na fila**. Os testes comparam os scores
do CatBoost com os resultados reproduzidos e conferem PostgreSQL, autorização,
API, relatório e contexto do assistente. Testes técnicos usam fixtures separadas
ou schemas temporários; as métricas ML vêm de registros reais.

Para testes sem conexão ao PostgreSQL, use `main.py testar`. Testes de
integração serão explicitamente ignorados nessa modalidade.

## 7. Abrir e atualizar

```powershell
.\.venv\Scripts\python.exe main.py iniciar
```

Acesse http://127.0.0.1:8000 e faça login. Mantenha o terminal aberto.
Após mudar o modelo ou a partição, encerre com Ctrl+C e execute novamente.
Use Ctrl+F5 no navegador para atualizar os arquivos de frontend em cache.

Fluxo demonstrável: **entradas D−1 no PostgreSQL → CatBoost → fila diária →
interface → revisão humana e explicação assistida**. Pedidos novos da loja
continuam demonstrativos; ainda não possuem os 111 atributos operacionais.

## Estudar e treinar a VER0.7 — opcional para abrir o sistema

Abra [03_catboost_ver07.ipynb](../Notebooks/03_catboost_ver07.ipynb).
O notebook é independente do 01/02 e reúne fontes, tratamento explicado,
EDA do treino, atributos D−1, treino CatBoost, baseline, avaliação e persistência.

1. Neste computador, selecione o kernel `.venv-ctgan`, ambiente de pesquisa já
   disponível. Não há uso de GAN nesse notebook. Em um computador novo, crie
   `.venv-ver07` e instale `requirements-ver07.txt`, conforme a etapa 2 deste guia.
2. Na célula **1.1**, confira os caminhos dos ZIPs Olist e rodovias. O padrão
   procura os arquivos em Downloads do usuário atual.
3. Use **Reiniciar Kernel → Executar Tudo**. Não execute outro notebook antes.
4. Confira o resumo calculado ao final. Os quatro artefatos de cada execução
   ficam em uma pasta própria sob `Modelos/notebook_ver07/`.

O notebook treina do zero com os parâmetros do piloto e confere a equivalência
com o modelo recebido. Não altera o PostgreSQL, a API, os notebooks antigos
ou o CatBoost ativo. Promover um modelo diferente exige uma revisão separada
do contrato de atributos e da avaliação; não basta copiar qualquer `.cbm`.

## Versão anterior e evidências

Para voltar à MLP, defina `ENTREGACERTA_MODELO=mlp` no `.env` e reinicie.
Os pesos, preprocessador e tabelas antigos foram preservados. Se estiver em
um computador novo sem a carga da MLP, siga o
[guia anterior](versoes/instalacao_mlp_pre_ver07.md).

Os resultados, limitações científicas e arquivos de auditoria estão em
[modelo_ver07.md](modelo_ver07.md). O teste da VER0.7 foi reutilizado na pesquisa;
seus números devem ser apresentados como exploratórios.

O código e a documentação anteriores à integração estão no ZIP
`Docs/versoes/antes_catboost_ver07_20261117.zip`. Extraia em outra pasta para
comparar antes de restaurar. Esse ZIP não é backup do PostgreSQL, das senhas
ou dos ambientes Python. Os pontos 0/1 anteriores continuam separados em
`EntregaCertaAI_Pontos`, na pasta irmã do repositório.
