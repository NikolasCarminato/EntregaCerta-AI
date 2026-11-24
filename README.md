# EntregaCerta AI 📦

Projeto do Bootcamp TCS que utiliza dados da Olist, Machine Learning e IA Generativa para apoiar a priorização de pedidos com risco de atraso.

**Stack:** Python, FastAPI, PostgreSQL, CatBoost, Gemini, HTML, CSS, JavaScript, Docker e Terraform.

## Instalar em um novo PC

### 1. Pré-requisitos

Instale **Python 3.12**, **Git** e **Docker Desktop**. Abra o Docker Desktop antes de preparar o banco.

### 2. Baixar e instalar

No PowerShell:

```powershell
git clone --branch rnn-train https://github.com/ProjetoBootcampTCS/EntregaCerta-AI.git
cd EntregaCerta-AI

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

A cópia do `.env` é apenas para a primeira instalação. Não sobrescreva uma configuração existente.

### 3. Configurar

No `.env`:

- Confira `POSTGRES_*` e defina sua senha local. A porta padrão é **5433**.
- Mantenha `ENTREGACERTA_MODELO=catboost_ver07` e `ENTREGACERTA_PARTICAO=teste`.
- Preencha `GEMINI_API_KEY` para utilizar o assistente.

Confira os dados em `Dados/Brazilian Logistic/` e os artefatos em `Modelos/catboost_ver07/`. **O modelo já está treinado; não é necessário executar notebooks.**

Nunca publique o `.env`.

### 4. Preparar o banco e criar o analista

```powershell
.\.venv\Scripts\python.exe main.py preparar-catboost
.\.venv\Scripts\python.exe main.py criar-analista --nome "Seu nome" --email "seuemail@exemplo.com"
.\.venv\Scripts\python.exe main.py verificar
```

A senha será solicitada no terminal. O cadastro público da aplicação cria somente contas de clientes.

## Executar o projeto

Com o Docker Desktop aberto, execute na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe main.py iniciar
```

Acesse **http://127.0.0.1:8000**. Mantenha o terminal aberto e use **Ctrl+C** para encerrar.

### Alternativa com Terraform e Docker

Com Terraform instalado (**>= 1.14 e < 2.0**):

```powershell
.\.venv\Scripts\python.exe infra/local/gerenciar.py iniciar
```

Acesse **http://localhost:8001**. Escolha apenas uma forma de execução.

Para conferir os testes:

```powershell
.\.venv\Scripts\python.exe main.py testar --postgres
```

## 💡 Skills para vibe coding

As orientações de desenvolvimento assistido por IA ficam em **`.agents/skills/`**, separadas em desenvolvimento geral, frontend, GenAI e backend/infraestrutura.

Exemplo:

> Use $entregacerta-frontend para melhorar os filtros de Pedidos, preservando a intro e o ML. Confira o funcionamento.

Se a skill não aparecer na ferramenta, informe o caminho do seu `SKILL.md`. Revise e teste o código gerado.

## Documentação e limites

Consulte o [guia completo de instalação](Docs/instalacao_novo_pc.md) e a [documentação do modelo](Docs/modelo_ver07.md).

A demonstração utiliza dados históricos. O score indica risco relativo, não probabilidade calibrada. A GenAI auxilia a interpretação; a decisão final é do analista.