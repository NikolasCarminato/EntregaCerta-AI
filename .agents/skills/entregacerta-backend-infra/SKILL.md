---
name: entregacerta-backend-infra
description: Implementar ou corrigir APIs, contas, consultas PostgreSQL e infraestrutura Docker/Terraform local do EntregaCerta AI, preservando dados, contratos de inferência e separação entre cliente e analista.
---

# Backend e infraestrutura

Aplicar o fluxo de [vibe coding](../entregacerta-vibe-code/SKILL.md). A raiz contém main.py; os módulos pertencem a src. Não executar arquivo com import relativo como script isolado.

## Mapa

- src/api.py: FastAPI, páginas, endpoints, cache e integração com inferência/relatório.
- src/acesso_web.py, loja_web.py, pedidos_web.py e esquema_web.sql: acesso, loja, catálogo e estrutura web.
- src/banco.py, catboost_ver07.py e inferencia.py: consulta e contrato do modelo. Não retreinar nem trocar artefatos como efeito colateral de uma funcionalidade web.
- Dockerfile, compose.yaml, infra/local/main.tf e infra/local/gerenciar.py: execução e infraestrutura local.

## API e dados

- Conferir requisição, resposta, validação e consumidores antes de mudar endpoint. Preservar campos necessários ao frontend, assistente e PDF.
- Aplicar autorização no servidor. Cadastro público cria cliente; analistas são administrados pela equipe/empresa. Cliente só acessa seus pedidos/conversas; relatório analítico é exclusivo do analista.
- Usar SQL parametrizado e transações. Conferir unicidade de order_id e cardinalidade de joins; agregar itens/pagamentos antes de juntar ao nível do pedido.
- Preservar schemas e histórico. As entradas VER0.7 estão em modelo_ver07.pedidos; desfechos de auditoria não são entradas nem previsões pré-programadas.
- Consultas e relatórios usam o lote/modelo correto. Mudanças de configuração precisam considerar caches; não reiniciar serviços da apresentação sem necessidade para o pedido.
- Não exibir senhas, hashes, chaves, tokens ou conteúdo do .env. O .env.example contém exemplos sem segredos. Autenticação não equivale a certificação legal.

## Operação local

Consultar o [guia de instalação](../../../Docs/instalacao_novo_pc.md). Comandos existentes, executados na raiz:

    .\.venv\Scripts\python.exe main.py verificar
    .\.venv\Scripts\python.exe main.py testar
    .\.venv\Scripts\python.exe main.py testar --postgres
    .\.venv\Scripts\python.exe main.py iniciar
    .\.venv\Scripts\python.exe infra/local/gerenciar.py iniciar

- verificar confere banco e inferência sem chamar GenAI.
- testar executa a suíte; --postgres habilita integração em schemas temporários.
- main.py iniciar usa a porta padrão 8000; o gerenciador Terraform usa 8001.
- Escolher o comando pertinente; a lista não é uma sequência obrigatória. Se já houver serviço aberto, identificar processo/contêiner e porta antes de iniciar outra instância. Consultar --help para opções atuais.

Terraform gerencia os recursos locais, Docker executa contêineres e PostgreSQL persiste os dados. O comando planejar do gerenciador pode preparar imagem/estado: ler seu fluxo antes de tratá-lo como inspeção sem efeitos.

- Ao mudar infraestrutura, revisar plano e proteção em validar_plano. Preservar banco e volume; não usar destroy ou remoção de volumes como solução rotineira.
- Recriar a aplicação preservando o banco somente quando necessário e autorizado pelo pedido. Manter estado Terraform e segredos fora do Git.
- O requisito atual não demanda nuvem nem recursos pagos. Não acrescentar deploy em nuvem sem solicitação.

## Conferir

Selecionar testes afetados: test_acesso_web.py, test_pedidos_web.py, test_inicializacao.py, test_infra_local.py, test_catboost_ver07.py ou test_relatorio_pdf.py, em tests/.

Para contas, testar também a recusa de acesso de outro usuário. Integrações PostgreSQL continuam em schemas temporários; não apagar dados reais para limpar fixtures. Informar testes pulados por falta de banco.

Mudanças de infraestrutura exigem conferir serviço e preservação do volume. Alterar somente estas instruções não exige iniciar contêineres, chamar Gemini ou treinar modelos.
