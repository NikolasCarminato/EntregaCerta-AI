FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HOME=/tmp
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY infra/local/requirements.txt /tmp/requirements.txt
# A aplicação importa PyTorch para compatibilidade com a MLP preservada.
# A distribuição CPU evita baixar bibliotecas CUDA sem uso neste projeto.
RUN pip install --no-cache-dir torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip check

COPY src/ ./src/
COPY frontend/ ./frontend/
COPY main.py ./
COPY Modelos/catboost_ver07/ ./Modelos/catboost_ver07/
COPY Modelos/preprocessador_rede.joblib Modelos/rede_neural.pt ./Modelos/
COPY ["Dados/Brazilian Logistic/olist_products_dataset.csv", "Dados/Brazilian Logistic/olist_order_items_dataset.csv", "./Dados/Brazilian Logistic/"]

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/auth/sessao', timeout=4)"
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
