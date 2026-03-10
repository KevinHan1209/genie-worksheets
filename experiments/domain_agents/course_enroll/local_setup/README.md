# Local Setup for Course Enroll Demo

This repo does not include a full production sample database dump. This folder provides a lightweight local setup so the course-enroll frontend can run end-to-end.

## 1) Start PostgreSQL on localhost:5432

If you already have Postgres running locally, skip to step 2.

Example with Docker:

```bash
docker run -d \
  --name genie-course-postgres \
  -e POSTGRES_USER=select_user \
  -e POSTGRES_PASSWORD=select_user \
  -e POSTGRES_DB=course_assistant \
  -p 5432:5432 \
  postgres:14
```

## 2) Seed minimal `course_assistant` data

From repo root:

```bash
.venv312/bin/python "experiments/domain_agents/course_enroll/local_setup/seed_course_assistant_db.py" --reset
```

This creates and seeds:

- `courses`
- `offerings`
- `ratings`
- `programs`

## 3) Start embedding-compatible server on :8509

The original SUQL embedding stack requires `faiss` and `FlagEmbedding`, which are often missing in fresh environments.

For local dev, run the mock server:

```bash
.venv312/bin/python "experiments/domain_agents/course_enroll/local_setup/mock_embedding_server.py" --port 8509
```

It exposes:

- `POST /search` (SUQL-compatible)
- `GET /healthz`
- `POST /refresh` (reload text index from DB)

## 4) Run frontend

```bash
cd "experiments/domain_agents/course_enroll/frontend"
chainlit run app_course_enroll.py --port 8800
```

## Notes

- This dataset is synthetic and small, intended for dev sanity checks.
- If you update DB rows while the mock embedding server is running, call:

```bash
curl -X POST http://127.0.0.1:8509/refresh
```

