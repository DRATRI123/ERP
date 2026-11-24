# Private ERP — Python API and worker (same image, different command).
FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ERP_PDF_CHROMIUM_PATH=/usr/bin/chromium

# chromium: invoice PDFs (offline, JavaScript disabled). postgresql-client: pg_dump/pg_restore
# for backups when running on PostgreSQL. fonts: Indian scripts on printed documents.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium postgresql-client \
      fonts-noto-core fonts-noto-ui-core tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 erp && useradd --system --uid 10001 --gid erp --home /app erp
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[postgres]"
COPY alembic.ini ./
COPY alembic ./alembic

# Database (SQLite), backups and uploads live on volumes owned by the unprivileged user.
RUN mkdir -p /app/data /app/data/backups && chown -R erp:erp /app/data
USER erp
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
# --proxy-headers: trust X-Forwarded-For/Proto from Caddy only (FORWARDED_ALLOW_IPS is set to
# the Docker network in docker-compose.yml), so client IPs and https detection are correct.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--workers", "1", "--no-server-header"]
