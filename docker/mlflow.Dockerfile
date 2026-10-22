FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 MLFLOW_DISABLE_TELEMETRY=true
COPY requirements-mlflow-lock.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && pip check \
    && groupadd --gid 10001 mlflow && useradd --uid 10001 --gid 10001 --no-create-home mlflow \
    && mkdir -p /data/artifacts && chown -R mlflow:mlflow /data
WORKDIR /data
USER 10001:10001
EXPOSE 5500
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5500", "--workers", "1", "--backend-store-uri", "sqlite:////data/tracking.db", "--artifacts-destination", "/data/artifacts", "--allowed-hosts", "mlflow:5500,localhost:*,127.0.0.1:*"]
