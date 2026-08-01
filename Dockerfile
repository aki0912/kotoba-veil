FROM python:3.11.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
COPY benchmarks ./benchmarks
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install .

FROM python:3.11.12-slim-bookworm

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KOTOBA_VEIL_DATA_DIR=/data

RUN groupadd --system kotoba \
    && useradd --system --gid kotoba --home-dir /app kotoba \
    && mkdir -p /app /data \
    && chown -R kotoba:kotoba /app /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=kotoba:kotoba app /app/app

WORKDIR /app
USER kotoba
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
