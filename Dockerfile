FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app/.cache/home \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.cache/config \
    HF_HOME=/app/.cache/huggingface \
    HUGGINGFACE_HUB_CACHE=/app/.cache/huggingface/hub \
    TRANSFORMERS_CACHE=/app/.cache/huggingface/transformers \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    TORCH_HOME=/app/.cache/torch

WORKDIR /app

COPY requirements.txt ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && python -m pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --home-dir /app/.cache/home --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/.cache/home /app/.cache/config /app/index /app/repos \
    && chown -R appuser:appuser /app

FROM base AS ingest

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

USER appuser
CMD ["python", "ingest.py"]

FROM base AS runtime

USER appuser
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
