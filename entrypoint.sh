#!/usr/bin/env bash
set -euo pipefail

# Comentários de operação/segurança:
# - Garante que o secret exista e seja legível.
# - Inicializa/valida o DB ao iniciar o app (feito no import da api.db).
# - Mantém o usuário não-root.

: "${DB_PATH:=/data/keys.db}"
: "${DB_KEYFILE:=/run/secrets/db_key}"
: "${API_PORT:=8080}"
: "${UVICORN_WORKERS:=1}"

if [ ! -r "${DB_KEYFILE}" ]; then
  echo "FALHA: secret DB_KEYFILE (${DB_KEYFILE}) não legível." >&2
  exit 1
fi

# Sobe Uvicorn (app: api.main.app)
exec uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${API_PORT}" \
  --workers "${UVICORN_WORKERS}"
