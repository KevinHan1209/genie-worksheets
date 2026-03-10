#!/usr/bin/env python3
"""Lightweight `/search` server compatible with SUQL embedding interface.

This is a fallback for local dev when `faiss` / `FlagEmbedding` are unavailable.
It performs simple keyword overlap scoring over text fields in Postgres.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import psycopg2
from flask import Flask, jsonify, request


TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


PRIMARY_KEYS = {
    "courses": "course_id",
    "offerings": "course_id",
    "ratings": "rating_id",
    "programs": "program_id",
}


def tokenize(text: str) -> set[str]:
    return set(TOKEN_RE.findall((text or "").lower()))


class DbTextIndex:
    def __init__(self, cfg: DbConfig):
        self.cfg = cfg
        self._index: dict[str, dict[str, dict[Any, str]]] = {}

    def refresh(self):
        conn = psycopg2.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.user,
            password=self.cfg.password,
            dbname=self.cfg.database,
        )
        try:
            with conn.cursor() as cur:
                for table, pk in PRIMARY_KEYS.items():
                    cur.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                        """,
                        (table,),
                    )
                    cols = cur.fetchall()
                    if not cols:
                        continue

                    text_like = [
                        c for (c, t) in cols if t in {"text", "character varying", "ARRAY"}
                    ]
                    if pk not in [c for (c, _) in cols]:
                        continue
                    if not text_like:
                        continue

                    field_select = ", ".join([f'"{c}"' for c in text_like])
                    cur.execute(f'SELECT "{pk}", {field_select} FROM "{table}"')
                    rows = cur.fetchall()

                    table_index: dict[str, dict[Any, str]] = defaultdict(dict)
                    for row in rows:
                        row_id = row[0]
                        for i, col in enumerate(text_like, start=1):
                            value = row[i]
                            if isinstance(value, list):
                                value = " ".join(str(v) for v in value if v is not None)
                            elif value is None:
                                value = ""
                            else:
                                value = str(value)
                            table_index[col][row_id] = value
                    self._index[table] = dict(table_index)
        finally:
            conn.close()

    def doc_for(self, table: str, field: str, row_id: Any) -> str:
        return self._index.get(table, {}).get(field, {}).get(row_id, "")


def score_doc(doc: str, query: str) -> float:
    if not doc:
        return 0.0
    q = tokenize(query)
    d = tokenize(doc)
    if not q or not d:
        return 0.0
    return float(len(q & d))


def build_app(cfg: DbConfig):
    app = Flask(__name__)
    index = DbTextIndex(cfg)
    index.refresh()

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify({"ok": True})

    @app.route("/refresh", methods=["POST"])
    def refresh():
        index.refresh()
        return jsonify({"ok": True})

    @app.route("/search", methods=["POST"])
    def search():
        data = request.get_json(force=True, silent=False)
        id_list = data.get("id_list")
        field_query_list = data.get("field_query_list", [])
        top = int(data.get("top", 5))
        single_table = bool(data.get("single_table", True))

        if single_table:
            candidates = []
            for row_id in id_list or []:
                total = 0.0
                docs = []
                for field, query in field_query_list:
                    table, col = field
                    doc = index.doc_for(table, col, row_id)
                    docs.append(doc)
                    total += score_doc(doc, query)
                candidates.append((row_id, total, docs))
        else:
            join_ids = (id_list or {}).get("_id_join", [])
            candidates = []
            for i, join_id in enumerate(join_ids):
                total = 0.0
                docs = []
                for field, query in field_query_list:
                    table, col = field
                    table_ids = (id_list or {}).get(table, [])
                    if i >= len(table_ids):
                        continue
                    row_id = table_ids[i]
                    doc = index.doc_for(table, col, row_id)
                    docs.append(doc)
                    total += score_doc(doc, query)
                candidates.append((join_id, total, docs))

        candidates.sort(key=lambda x: x[1], reverse=True)
        if top > 0:
            candidates = candidates[:top]

        result = [[row_id, docs] for row_id, _, docs in candidates]
        return jsonify({"result": result})

    return app


def parse_args():
    parser = argparse.ArgumentParser(description="Run local SUQL-compatible mock embedding server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8509)
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-user", default="select_user")
    parser.add_argument("--db-password", default="select_user")
    parser.add_argument("--db-name", default="course_assistant")
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = DbConfig(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )
    app = build_app(cfg)
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
