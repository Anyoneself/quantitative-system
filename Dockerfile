FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    QUANT_DB_PATH=/app/data/app/quantitative_system.db \
    PUBLIC_DATA_RAW_DIR=/app/data/raw/http

WORKDIR /app

RUN groupadd --system quant \
    && useradd --system --gid quant --home-dir /app quant \
    && mkdir -p /app/data/app /app/data/raw/http \
    && chown -R quant:quant /app

COPY --chown=quant:quant pyproject.toml README.md ./
COPY --chown=quant:quant config ./config
COPY --chown=quant:quant scripts ./scripts
COPY --chown=quant:quant src ./src

RUN pip install --no-cache-dir . \
    && chmod +x /app/scripts/quant /app/scripts/docker-entrypoint.sh

USER quant

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
