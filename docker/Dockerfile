# Backend: FastAPI on uvicorn
FROM python:3.12-slim AS pydeps
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

FROM python:3.12-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl sqlite3 \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 1001 --home /app --shell /sbin/nologin tm

WORKDIR /app
COPY --from=pydeps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=pydeps /usr/local/bin /usr/local/bin
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data && chown -R tm:tm /app

USER tm
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8088/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
