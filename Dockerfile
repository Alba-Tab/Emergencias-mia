# Imagen de producción del servicio de análisis. Sin estado: la configuración llega por variables de entorno (AI_*).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY pyproject.toml constraints.txt ./
COPY app ./app
RUN pip install --constraint constraints.txt . \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mia

USER mia

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# keep-alive mayor que el del cliente HTTP del backend (30 s): así no reutiliza una conexión que uvicorn ya cerró.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75", "--no-server-header"]
