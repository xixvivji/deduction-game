#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x runtime/mlflow-venv/bin/mlflow ]; then
    echo 'MLflow 환경이 없습니다. docs/operations.md의 설치 절차를 실행하세요.' >&2
    exit 1
fi
mkdir -p runtime/mlflow
export MLFLOW_DISABLE_TELEMETRY=true
exec runtime/mlflow-venv/bin/mlflow server --host 127.0.0.1 --port 5500 --backend-store-uri sqlite:///runtime/mlflow/tracking.db --default-artifact-root ./runtime/mlflow/artifacts --workers 1
