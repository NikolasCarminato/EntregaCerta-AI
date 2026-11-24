# Histórico e decisões VER0.7

| Versão | Checkpoint | Precision | Recall | F1 | Seleção/escopo |
| --- | --- | ---: | ---: | ---: | --- |
| VER0.2 | D−2 | 47,80% | 80,80% | 60,07% | Otimização anterior, teste reutilizado |
| VER0.3 | D−1 | 47,92% | 92,41% | 63,11% | Referência anterior |
| VER0.6 reais | D−1 | 45,37% | 98,36% | 62,09% | Features externas reais, validação selecionou sem rodovias |
| VER0.7 seleção formal | D−1 | 45,47% | 99,40% | 62,40% | Modelo/limiar escolhidos pela validação |
| **VER0.7 piloto** | **D−1** | **55,86%** | **82,29%** | **66,55%** | **Cota 65% definida exploratoriamente depois de reutilizar o teste** |

Versões são preservadas sem sobrescrita. As diferentes estratégias usam o mesmo período de 2018 e não podem ser tratadas como evidências independentes.

## Heurísticas sem ML com a mesma cota de 65%/dia

| Regra | F1 validação | F1 teste |
| --- | ---: | ---: |
| order_age | 68,98% | 59,57% |
| in_transit_age | 67,65% | 48,38% |
| not_dispatched_then_age | 69,42% | 66,43% |
| days_after_shipping_limit | 69,57% | 56,56% |

**A seleção da heurística por validação** foi `days_after_shipping_limit` com F1 teste 56,56%. Outra heurística quase empatou com o CatBoost no teste, o que reforça a necessidade de prova independente.
