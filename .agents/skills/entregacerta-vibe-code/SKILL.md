---
name: entregacerta-vibe-code
description: Implementar e refinar funcionalidades do EntregaCerta AI a partir de pedidos em linguagem natural, com escopo claro, código compreensível e validação real. Usar também para orientar a equipe passo a passo, quando solicitado.
---

# Vibe coding no EntregaCerta AI

Transformar a intenção da equipe em uma mudança utilizável e verificável. A equipe está aprendendo Python e precisa explicar à banca o código e as escolhas.

## Entender antes de editar

- Localizar a raiz pelo [README](../../../README.md), main.py, frontend/ e src/. Não trabalhar nas cópias vizinhas EntregaCertaAI_Pontos.
- Conferir instruções locais, git status e os arquivos do fluxo solicitado. Preservar alterações da equipe; não reorganizar o repositório nem criar versões por conta própria.
- Se o pedido for “nos guie”, entregar um passo executável e esperar o resultado. Se for “faça”, concluir e conferir o trabalho autorizado. Perguntas sobre uma ideia não autorizam sua implementação.
- Explicar brevemente o comportamento esperado, os arquivos envolvidos e como verificar. Resolver escolhas rotineiras sem nova aprovação; perguntar apenas pelo requisito que impeça uma implementação correta.
- Respeitar limites como “só o front”, “sem mexer na intro” e “sem mexer no ML”. Se houver dependência de outra camada, concluir a parte independente e explicar a ampliação necessária.

## Contexto atual

Referência revisada em 23/11/2026; conferir código e configuração, pois o projeto pode evoluir.

- HTML/CSS/JavaScript no front; Python, FastAPI e Uvicorn no backend; PostgreSQL; Docker e Terraform local. Reutilizar a stack existente.
- Modelo integrado: CatBoost VER0.7 em D−1. MLP/PyTorch permanece como histórico ou modo alternativo. Ler [modelo_ver07.md](../../../Docs/modelo_ver07.md) antes de alterar inferência ou explicar resultados.
- D−1 é o início do dia anterior à promessa. Só pedidos elegíveis ainda pendentes são pontuados. A fila usa arredondamento para cima por dia; consultar a política do modelo ativo em vez de duplicá-la no front.
- Score é risco relativo, não probabilidade calibrada. “Não pontuado” não significa risco zero. O teste foi reutilizado na pesquisa: métricas são exploratórias e precisam identificar coorte e checkpoint.
- O dashboard reproduz dados históricos reais da Olist. Loja e rastreamento demonstrativos são separados; seus pedidos não recebem previsão real sem integração validada dos atributos.
- A GenAI explica; o analista decide. Não inventar ações realizadas, causas ou redução de atrasos. ROI hipotético deve ser identificado como projeção.
- O artigo foi retirado do escopo pela equipe. Manter registros técnicos e apresentação; não recriar artigo por exigência de anotação antiga.

## Implementar e conferir

1. Seguir o fluxo da tela até API, consulta e resposta; identificar os contratos afetados.
2. Fazer a menor alteração coerente, reutilizando módulos. Evitar frameworks, agentes, serviços e abstrações sem benefício concreto.
3. Usar nomes claros e comentários sobre decisões do domínio. A aplicação usa módulos de src/ e modelos salvos; não deve exigir executar notebooks para abrir.
4. Tratar sucesso, carregamento, vazio e erro. Fixtures técnicas são identificadas; números inventados não são resultados do modelo.
5. Conferir o fluxo principal e os casos afetados. Testar comportamento, não detalhes internos ou a presença literal de uma frase.

Ler apenas a orientação pertinente: [frontend](../entregacerta-frontend/SKILL.md), [GenAI](../entregacerta-genai/SKILL.md) ou [backend/infraestrutura](../entregacerta-backend-infra/SKILL.md). Não carregar todas automaticamente.

Para pesquisa de ML, separar experimentos do modelo ativo. Ajustar transformações somente no treino e usar dados disponíveis no checkpoint. Dados sintéticos só entram em experimentos explicitamente pedidos e identificados; não misturá-los à avaliação real ou à base operacional. Não prometer desempenho antes de medi-lo.

## Entregar à equipe

- Informar em português simples: mudança, arquivos, como conferir, testes executados e pendências. Um teste de API não comprova o visual; mocks não comprovam disponibilidade do Gemini.
- Reutilizar [desenvolvimento.md](../../../Docs/desenvolvimento.md) e README para decisões relevantes. Evitar novos MDs/JSONs sem função concreta.
- Preservar segredos, dados, modelos, volume PostgreSQL e identidade Git da equipe. A skill não autoriza commits, publicação, custos externos ou envio a terceiros além do pedido vigente.
