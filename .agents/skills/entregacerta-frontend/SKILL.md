---
name: entregacerta-frontend
description: Alterar páginas, componentes e animações HTML/CSS/JS do EntregaCerta AI preservando sua identidade visual, navegação e integração real com a API. Aplicar em pedidos de interface ou experiência de uso.
---

# Frontend EntregaCerta AI

Aplicar o fluxo de [vibe coding](../entregacerta-vibe-code/SKILL.md). Trabalhar nos arquivos servidos por src/api.py, não em ZIPs ou versões antigas.

## Mapa

- frontend/index.html, style.css e script.js: início, identidade compartilhada e animações.
- dashboard.html, dashboard.css, pedidos.html, pedidos.css e pedidos.js: área do analista e consulta.
- entrar.html, entrar.js, auth.js, conta.css, cliente.html e cliente.js: acesso e área do cliente.
- assistente.js, assistente.css, relatorio.js e operacao.js: assistente, exportação e operação. Conferir os imports reais.
- rastreio-demo.js e rastreio-demo.css: demonstração sem consulta a transportadora real.

## Identidade e movimento

- Reutilizar os tokens de style.css: verde-lima (--lime), fundo escuro (--dark), superfícies claras (--paper), tipografia e espaçamentos. O CSS é a fonte atual; não redesenhar a marca ao corrigir um componente.
- Preservar menu lateral, logo, botões e hierarquia. Consultar Docs/prompt_frontend.txt para reconstrução visual, confrontando-o com o código atual.
- A intro está aprovada: caixa fechada até o clique, flutuação e inclinação próprias. Mudanças em outras páginas ou na caixa decorativa não devem alterar essa sequência.
- A caixa da página inicial tem movimento suave independente da intro. Separar seletores, transformações e eventos para não acumular efeitos.
- Preservar o slogan da página ao mudar frases da intro. As entradas durante a rolagem podem se repetir, mantendo leitura e cliques utilizáveis.
- Respeitar prefers-reduced-motion; evitar listeners duplicados e animações contínuas fora da área visível.
- Manter o botão do assistente acessível nas páginas, exceto durante a intro. Evitar sobreposição com menus, diálogos e teclado móvel.

## Fluxo real e acessibilidade

- Usar endpoints e campos reais. Filtros da lista não devem recalcular a fila sobre o subconjunto visível, salvo mudança explícita de regra implementada no backend.
- Tratar carregamento, erro e lista vazia. Painel e consulta continuam funcionando quando a GenAI falhar.
- Distinguir dados demonstrativos, histórico Olist, score e “Não pontuado”; não converter score em chance calibrada.
- Usar labels, elementos semânticos, foco visível e teclado. Escape fecha diálogos e o foco retorna à origem. Não remover todo contorno de foco para corrigir um contorno decorativo.
- Menu oculto não protege permissões: contas, pedidos e relatório exigem autorização no servidor.
- Renderizar respostas da IA e texto de usuário com segurança, sem HTML bruto não confiável.

## Conferir

Testar o fluxo alterado em desktop e largura estreita; observar navegação direta/voltar, foco, rolagem e erros de console/rede quando houver ferramenta de navegador. Para animações, conferir movimento reduzido e preservar o comportamento da intro.

Sem navegador ou sessão autenticada, fazer as verificações estáticas possíveis e informar que a inspeção visual ficou pendente. Não simular integração bem-sucedida com dados fictícios.
