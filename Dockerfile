FROM node:22-bookworm@sha256:82693da96a4e38b8c0e2bf0c5df58fcb86d2d25e5a235c07748f09bfa38567c2 AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS game
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt && pip check
RUN groupadd --gid 10001 game && useradd --uid 10001 --gid 10001 --no-create-home game \
    && mkdir -p /app/runtime && chown game:game /app/runtime
COPY game ./game
COPY frontend/index.html frontend/ops.html ./frontend/
COPY frontend/assets ./frontend/assets
COPY --from=frontend /ui/node_modules/vue/dist/vue.global.prod.js ./frontend/node_modules/vue/dist/vue.global.prod.js
COPY reports/claim-guard-reviewed-v2.json ./reports/claim-guard-reviewed-v2.json
COPY tools/container_entrypoint.py tools/log_evaluation_mlflow.py ./tools/
COPY tests ./tests
USER 10001:10001
EXPOSE 8200
ENTRYPOINT ["python", "-m", "tools.container_entrypoint"]
CMD ["uvicorn", "game.server:app", "--host", "0.0.0.0", "--port", "8200", "--workers", "1", "--no-proxy-headers"]
