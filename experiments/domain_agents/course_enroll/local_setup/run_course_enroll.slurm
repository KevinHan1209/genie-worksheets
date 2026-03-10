#!/usr/bin/env bash
#SBATCH --job-name=course-enroll-demo
#SBATCH --output=course-enroll-%j.out
#SBATCH --error=course-enroll-%j.err
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G

set -euo pipefail

# ---- configurable knobs (override with env vars) ----
ROOT_DIR="${ROOT_DIR:-$SLURM_SUBMIT_DIR}"
VENV_PATH="${VENV_PATH:-$ROOT_DIR/.venv312}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-select_user}"
DB_PASSWORD="${DB_PASSWORD:-select_user}"
DB_NAME="${DB_NAME:-course_assistant}"
EMBED_HOST="${EMBED_HOST:-127.0.0.1}"
EMBED_PORT="${EMBED_PORT:-8509}"
CHAINLIT_PORT="${CHAINLIT_PORT:-8800}"
START_POSTGRES_DOCKER="${START_POSTGRES_DOCKER:-0}"   # set to 1 to auto-start docker postgres
RESET_DB="${RESET_DB:-1}"                               # set to 0 to keep existing data

DOCKER_CONTAINER_NAME="genie-course-postgres-${SLURM_JOB_ID:-manual}"
MOCK_EMBED_PID=""

cleanup() {
  set +e
  if [[ -n "${MOCK_EMBED_PID}" ]]; then
    kill "${MOCK_EMBED_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${START_POSTGRES_DOCKER}" == "1" ]]; then
    docker rm -f "${DOCKER_CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> ROOT_DIR=${ROOT_DIR}"
cd "${ROOT_DIR}"

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "ERROR: virtualenv not found at ${VENV_PATH}"
  exit 1
fi

source "${VENV_PATH}/bin/activate"

if ! command -v pg_isready >/dev/null 2>&1; then
  echo "ERROR: pg_isready not found. Install PostgreSQL client tools first."
  exit 1
fi

echo "==> Checking PostgreSQL at ${DB_HOST}:${DB_PORT}"
if ! pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1; then
  if [[ "${START_POSTGRES_DOCKER}" == "1" ]]; then
    if ! command -v docker >/dev/null 2>&1; then
      echo "ERROR: START_POSTGRES_DOCKER=1 but docker is unavailable."
      exit 1
    fi
    echo "==> Starting dockerized PostgreSQL"
    docker run -d \
      --name "${DOCKER_CONTAINER_NAME}" \
      -e POSTGRES_USER="${DB_USER}" \
      -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
      -e POSTGRES_DB="${DB_NAME}" \
      -p "${DB_PORT}:5432" \
      postgres:14 >/dev/null

    for _ in $(seq 1 30); do
      if pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  else
    cat <<EOF
ERROR: PostgreSQL is not reachable at ${DB_HOST}:${DB_PORT}.

Either:
  1) start Postgres yourself, or
  2) re-run with START_POSTGRES_DOCKER=1 (if docker is allowed).
EOF
    exit 1
  fi
fi

echo "==> Seeding local course_assistant database"
SEED_ARGS=(
  --host "${DB_HOST}"
  --port "${DB_PORT}"
  --user "${DB_USER}"
  --password "${DB_PASSWORD}"
  --database "${DB_NAME}"
)
if [[ "${RESET_DB}" == "1" ]]; then
  SEED_ARGS+=(--reset)
fi
python "experiments/domain_agents/course_enroll/local_setup/seed_course_assistant_db.py" "${SEED_ARGS[@]}"

echo "==> Starting mock embedding server on ${EMBED_HOST}:${EMBED_PORT}"
python "experiments/domain_agents/course_enroll/local_setup/mock_embedding_server.py" \
  --host "${EMBED_HOST}" \
  --port "${EMBED_PORT}" \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --db-name "${DB_NAME}" &
MOCK_EMBED_PID=$!

echo "==> Waiting for embedding server health"
for _ in $(seq 1 30); do
  if curl -sf "http://${EMBED_HOST}:${EMBED_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://${EMBED_HOST}:${EMBED_PORT}/healthz" >/dev/null 2>&1; then
  echo "ERROR: mock embedding server did not become healthy."
  exit 1
fi

echo "==> Launching Chainlit frontend on port ${CHAINLIT_PORT}"
cd "experiments/domain_agents/course_enroll/frontend"
python -m chainlit run app_course_enroll.py --port "${CHAINLIT_PORT}"
