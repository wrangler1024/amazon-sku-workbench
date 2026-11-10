# 店铺邮箱 Web 客户端 store_mail_web
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE NOTICE requirements.lock ./
COPY src ./src
COPY openapi.yaml ./

RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

ENV STORE_MAIL_WEB_HOST=0.0.0.0 \
    STORE_MAIL_WEB_PORT=8789 \
    STORE_MAIL_WEB_DB_PATH=/app/var/store_mail_web.sqlite3

VOLUME ["/app/var"]
EXPOSE 8789

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8789/healthz', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "store_mail_web.main", "serve"]
