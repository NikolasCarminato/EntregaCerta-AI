# Guia didático para a banca — VER0.7

**Qual é o problema?** Antecipar quais encomendas do e-commerce podem chegar depois da data prometida.

**O que entra na IA?** Dados internos da compra, produto, frete, geografia, histórico de vendedores e rotas, clima histórico observado e já disponível, feriados, perfil comercial que já existia na compra e status de despacho até a data da consulta.

**O que não entra?** Data de entrega futura, chuva futura, resultado de outros pedidos que ainda não foram concluídos ou cadastro comercial posterior à compra.

**Por que CatBoost?** Modelo de árvores adequado a dados tabulares com muitas variáveis numéricas e categóricas; foi comparado com LightGBM nesta rodada. Não presumimos que mais parâmetros sempre melhoram.

**Por que D−1?** As informações de despacho e tempo já transcorrido são mais úteis perto do prazo; em troca, resta menos tempo para agir. D−2 permanece na arquitetura como alerta antecipado.

**Por que limitar os alertas a 65% dos pedidos pendentes?** Um limite operacional obriga o sistema a priorizar o risco. Sem limite, a regra de alertar todos já alcança Recall 100%, mas com excesso de alarmes falsos. Em D−1, a política de 65% alcançou 82,29% Recall, 55,86% Precision e 66,55% F1 no teste exploratório.

**O modelo foi realmente melhor que uma regra simples?** Ganhou 4.27 p.p. de F1 da regra de alertar *todos*; entretanto, uma regra simples alternativa com o mesmo orçamento chegou a F1 66,43% (CatBoost: 66,55%), então a superioridade do ML sobre essa alternativa não foi demonstrada.

**Por que a VER0.7 ainda é experimental?** O limite de 65% foi explorado no mesmo teste de 2018 visto nas versões anteriores. Isso cria risco de otimização ao teste. Para aprovação definitiva, precisamos congelar tudo e testar em dados verdadeiramente inéditos.

**Como tratamos os dados externos?** INMET → médias diárias por estado e janela defasada, sem chuva futura; Zenodo → códigos IBGE e pares dirigidos com 15,70% de cobertura e rede 2024; Marketing Funnel → junção pelo vendedor apenas para negócios fechados antes da compra.

**Por que não usaram PCA?** CatBoost não precisa de redução linear por padrão; o custo-benefício maior veio do contexto logístico e da estratégia de alertas.

**O que significa cada métrica?** Precision: chance de um alerta emitido estar correto. Recall: proporção de atrasos reais encontrados. F1: equilíbrio harmônico entre as duas. Acima de 65% de F1 **não** equivale a 65% de pedidos corretos no total.

**Próximos riscos:** dados antigos 2016–2018, mudança de taxa de atrasos ao longo do tempo, mapeamento rodoviário parcial, clima estadual defasado e ausência de rastreamento logístico granular real.
