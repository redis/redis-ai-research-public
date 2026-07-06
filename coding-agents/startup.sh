#!/usr/bin/env bash
# Bring up Postgres (in Docker) and launch the FastAPI service.
# Requires: docker, uv, and OPENAI_API_KEY exported in your shell.
set -euo pipefail

CONTAINER=${CONTAINER:-agent-pg}
PG_PORT=${PG_PORT:-5433}
APP_HOST=${APP_HOST:-127.0.0.1}
APP_PORT=${APP_PORT:-8000}
DB_URL="postgresql://agent:agent@127.0.0.1:${PG_PORT}/agent"

cd "$(dirname "$0")"

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "ERROR: OPENAI_API_KEY is not set in this shell." >&2
  echo "       Export it (or source ~/.zshrc) before running this script." >&2
  exit 1
fi

if ! docker info > /dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running. Start Docker Desktop and retry." >&2
  exit 1
fi

# Reuse the container if it's already there; otherwise create it.
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Postgres container '$CONTAINER' is already running."
elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Starting existing Postgres container '$CONTAINER'..."
  docker start "$CONTAINER" > /dev/null
else
  echo "Creating Postgres container '$CONTAINER' on port ${PG_PORT}..."
  docker run -d --name "$CONTAINER" \
    -e POSTGRES_USER=agent -e POSTGRES_PASSWORD=agent -e POSTGRES_DB=agent \
    -p "${PG_PORT}:5432" postgres:16 > /dev/null
fi

echo -n "Waiting for Postgres to accept connections"
for i in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U agent > /dev/null 2>&1; then
    echo " — ready (${i}s)."
    break
  fi
  echo -n "."
  sleep 1
  if [ "$i" = "30" ]; then
    echo
    echo "ERROR: Postgres did not become ready in 30s." >&2
    exit 1
  fi
done

export DATABASE_URL="$DB_URL"
echo "DATABASE_URL=$DATABASE_URL"
echo "Launching uvicorn on http://${APP_HOST}:${APP_PORT} (Ctrl+C to stop)"
echo
exec uv run uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT" --reload
