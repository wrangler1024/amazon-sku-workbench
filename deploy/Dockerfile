FROM node:22-bookworm-slim AS frontend
WORKDIR /build/apps/editor
COPY apps/editor/package*.json ./
RUN npm ci
COPY apps/editor ./
RUN npm run build
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY services/amazon-aigc-workbench-service/pyproject.toml ./
COPY services/amazon-aigc-workbench-service/src ./src
COPY services/amazon-aigc-workbench-service/sql ./sql
RUN pip install --no-cache-dir . && playwright install --with-deps chromium
COPY --from=frontend /build/apps/editor/dist ./web/dist
RUN groupadd -g 18098 aigc && useradd -u 18098 -g aigc -m aigc && mkdir -p /data/assets && chown -R aigc:aigc /data
ENV AIGC_STORAGE_ROOT=/data/assets AIGC_WEB_ROOT=/app/web/dist PYTHONPATH=/app/src
USER aigc
EXPOSE 8798
CMD ["uvicorn","amazon_aigc.api:create_app","--factory","--host","0.0.0.0","--port","8798"]
