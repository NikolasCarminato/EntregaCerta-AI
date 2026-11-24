from pathlib import Path
import os

import joblib
import numpy as np
import pandas as pd
import torch
from torch import nn

from .tratar_dados_ml import limpar_atributos


def prever_pedidos(pedidos, pacote, modelo):
    if pacote.get('tipo') == 'catboost_ver07':
        from .catboost_ver07 import prever
        return prever(pedidos, pacote, modelo)

    atributos, _ = limpar_atributos(pedidos)

    matriz = pacote["preprocessador"].transform(
        atributos[pacote["colunas_entrada"]]
    ).astype(np.float32)

    modelo.eval()

    with torch.no_grad():
        scores = torch.sigmoid(
            modelo(torch.from_numpy(matriz)).squeeze(1)
        ).numpy()

    resultado = pedidos[["order_id"]].copy()
    resultado["score_atraso"] = scores

    return resultado


def priorizar_pedidos(previsoes, fracao):
    if 'checkpoint_at' in previsoes:
        from .catboost_ver07 import priorizar
        return priorizar(previsoes, fracao)
    resultado = previsoes.copy().reset_index(drop=True)

    quantidade = int(len(resultado) * fracao)
    selecionados = resultado.nlargest(
        quantidade, "score_atraso"
    ).index

    resultado["prioridade"] = 0
    resultado.loc[selecionados, "prioridade"] = 1

    return resultado

def tipo_modelo():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    return os.getenv('ENTREGACERTA_MODELO', 'catboost_ver07')


def carregar_modelos(tipo=None):
    tipo = tipo or tipo_modelo()
    if tipo == 'catboost_ver07':
        from .catboost_ver07 import carregar
        return carregar()
    if tipo != 'mlp':
        raise ValueError('ENTREGACERTA_MODELO deve ser catboost_ver07 ou mlp.')
    # Localiza a pasta Modelos a partir deste arquivo.
    pasta = Path(__file__).resolve().parents[1] / "Modelos"

    pacote = joblib.load(pasta / "preprocessador_rede.joblib")
    camada1, camada2 = pacote["camadas_ocultas"]

    # Reconstrói a mesma estrutura usada no treinamento.
    modelo = nn.Sequential(
        nn.Linear(pacote["numero_entradas"], camada1),
        nn.ReLU(),
        nn.Dropout(pacote["dropout"]),
        nn.Linear(camada1, camada2),
        nn.ReLU(),
        nn.Linear(camada2, 1)
    )

    pesos = torch.load(
        pasta / "rede_neural.pt",
        map_location="cpu",
        weights_only=True
    )

    modelo.load_state_dict(pesos)
    modelo.eval()

    return pacote, modelo

if __name__ == "__main__":
    raiz = Path(__file__).resolve().parents[1]

    # Carrega o pré-processador e a rede.
    pacote, modelo = carregar_modelos()

    # Lê os pedidos reais preparados para a demonstração.
    if pacote.get('tipo') == 'catboost_ver07':
        from .catboost_ver07 import consultar
        pedidos = consultar(pacote)
    else:
        pedidos = pd.read_csv(raiz / "Dados" / "pedidos_demo_validacao.csv", dtype={"order_id": "string"})

    # Calcula os scores e seleciona a fila.
    previsoes = prever_pedidos(pedidos, pacote, modelo)

    resultado = priorizar_pedidos(
        previsoes,
        pacote["fracao_fila"]
    )

    # Salva identificador, score e prioridade de cada pedido.
    caminho_saida = (raiz / 'Modelos' / 'catboost_ver07' / 'previsoes_backend.csv'
                     if pacote.get('tipo') == 'catboost_ver07'
                     else raiz / "Dados" / "previsoes_demo_validacao.csv")
    resultado.to_csv(caminho_saida, index=False)

    print("Previsão e priorização executadas!")
    print(f"Pedidos avaliados: {len(resultado)}")
    print(f"Pedidos na fila: {int(resultado['prioridade'].sum())}")
    print("Resultado salvo em:", caminho_saida)

