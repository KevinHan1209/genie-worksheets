# Local Setup for Insurance Lead Follow-Up

This setup runs the Insurance Lead Follow-Up domain with:

- Local worksheet spec: `experiments/agents/insurance_lead_followup/insurance_lead_spec.json`
- Local Postgres data source: database `insurance`

## 1) Start PostgreSQL on localhost:5432

Example with Docker:

```bash
docker run -d \
  --name genie-insurance-postgres \
  -e POSTGRES_USER=select_user \
  -e POSTGRES_PASSWORD=select_user \
  -e POSTGRES_DB=insurance \
  -p 5432:5432 \
  postgres:14
```

## 2) Seed the default lead data

From repo root:

```bash
python experiments/agents/insurance_lead_followup/local_setup/seed_insurance_db.py --reset
```

This creates the schema and inserts a single deterministic lead (Kevin with a quote for 742 Evergreen Terrace, Springfield, IL).

## 3) Run the mock embedding server on :8509

```bash
python experiments/agents/insurance_lead_followup/local_setup/mock_embedding_server.py \
  --port 8509 --db-name insurance
```

## 4) Optional env overrides

Defaults match the local setup above. Override only if needed:

```bash
export INSURANCE_DB_HOST=127.0.0.1
export INSURANCE_DB_PORT=5432
export INSURANCE_DB_USER=select_user
export INSURANCE_DB_PASSWORD=select_user
export INSURANCE_DB_NAME=insurance
```

## 5) Run Chainlit frontend

```bash
cd experiments/agents/insurance_lead_followup/frontend
chainlit run app_insurance.py --port 8801
```

## 6) Chat DB behavior (control + per-chat copies)

The frontend now uses schema isolation:

- `insurance_control` schema is the shared control state.
- each chat session gets its own schema clone (`insurance_chat_<user_id...>`).
- each new chat starts from the same control state.
- per-chat mutations do **not** affect control unless you opt in to promotion.

At chat end, a DB export is written to:

- `experiments/agents/insurance_lead_followup/frontend/user_conversation/<user_id>/db_state.json`

Use these env vars to control behavior:

```bash
# Optional: control schema name (default: insurance_control)
export INSURANCE_CONTROL_SCHEMA=insurance_control

# Optional: chat schema prefix (default: insurance_chat)
export INSURANCE_CHAT_SCHEMA_PREFIX=insurance_chat

# Optional: promote chat schema back to control at chat end (default: off)
export INSURANCE_PROMOTE_CHAT_TO_CONTROL_ON_END=1

# Optional: drop chat schema at chat end (default: off)
export INSURANCE_DROP_CHAT_SCHEMA_ON_END=1

# Optional: old behavior - reset public schema DB on chat start/end (default: off)
export INSURANCE_RESET_DB_ON_CHAT=1
```
