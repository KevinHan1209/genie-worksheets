#!/usr/bin/env python3
"""Create and seed a minimal local `course_assistant` Postgres database.

This is an intentionally small dataset for local development and demo flows.
It is not the original research dataset.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable

import psycopg2
from psycopg2.extras import execute_values


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


COURSES = [
    (
        1001,
        4,
        "Introduction to Programming",
        "Letter or Credit/No Credit",
        ["CS 101"],
        ["WAY-SMA"],
        3,
        "Learn core programming concepts using Python. Includes functions, loops, and data structures.",
        8.5,
        ["algorithmic_analysis"],
        True,
        ["systems"],
        [],
    ),
    (
        1002,
        4,
        "Object-Oriented Software Development",
        "Letter or Credit/No Credit",
        ["CS 102"],
        ["WAY-SMA"],
        3,
        "Build medium-size software systems with object-oriented design and testing.",
        9.0,
        ["principles_of_computer_systems"],
        True,
        ["systems"],
        ["CS 101"],
    ),
    (
        1003,
        4,
        "Data Structures and Algorithms",
        "Letter or Credit/No Credit",
        ["CS 103"],
        ["WAY-SMA"],
        4,
        "Programming-intensive treatment of data structures, complexity, and algorithmic problem solving.",
        10.0,
        ["algorithmic_analysis"],
        True,
        ["formal_foundations"],
        ["CS 101"],
    ),
    (
        1004,
        4,
        "Natural Language Processing with Deep Learning",
        "Letter or Credit/No Credit",
        ["CS 224N"],
        ["WAY-SI"],
        3,
        "Methods for processing language with deep learning models for sequence labeling, translation, and QA.",
        11.0,
        ["probability"],
        True,
        ["learning_and_modeling"],
        ["CS 103"],
    ),
    (
        1005,
        3,
        "Databases",
        "Letter or Credit/No Credit",
        ["CS 145"],
        ["WAY-SI"],
        3,
        "Relational data modeling, SQL query optimization, transactions, and database-backed application development.",
        8.0,
        ["principles_of_computer_systems"],
        True,
        ["systems"],
        ["CS 103"],
    ),
    (
        1006,
        4,
        "Machine Learning",
        "Letter or Credit/No Credit",
        ["CS 229"],
        ["WAY-SI"],
        3,
        "Supervised and unsupervised learning, optimization, and statistical modeling.",
        10.5,
        ["probability"],
        True,
        ["learning_and_modeling"],
        ["CS 103"],
    ),
]

OFFERINGS = [
    (1001, ["Monday", "Wednesday"], "09:30", "10:50", ["Ada Lovelace"], "autumn"),
    (1002, ["Tuesday", "Thursday"], "11:00", "12:20", ["Grace Hopper"], "winter"),
    (1003, ["Monday", "Wednesday"], "13:30", "14:50", ["Edsger Dijkstra"], "autumn"),
    (1004, ["Tuesday", "Thursday"], "15:00", "16:20", ["Christopher Manning"], "spring"),
    (1005, ["Monday", "Wednesday"], "10:00", "11:20", ["Jennifer Widom"], "winter"),
    (1006, ["Tuesday", "Thursday"], "13:30", "14:50", ["Andrew Ng"], "spring"),
]

RATINGS = [
    (
        5001,
        1001,
        ["Ada Lovelace"],
        4.3,
        180,
        202301,
        2023,
        2023,
        "autumn",
        ["Great intro programming class.", "Lots of coding practice and clear lectures."],
    ),
    (
        5002,
        1002,
        ["Grace Hopper"],
        4.1,
        140,
        202401,
        2024,
        2024,
        "winter",
        ["Good software engineering habits.", "Projects were practical and useful."],
    ),
    (
        5003,
        1003,
        ["Edsger Dijkstra"],
        4.6,
        210,
        202301,
        2023,
        2023,
        "autumn",
        ["Tough but very rewarding.", "Excellent for algorithmic programming skills."],
    ),
    (
        5004,
        1004,
        ["Christopher Manning"],
        4.7,
        260,
        202403,
        2024,
        2024,
        "spring",
        ["Best NLP course.", "Deep learning for language done right."],
    ),
    (
        5005,
        1005,
        ["Jennifer Widom"],
        4.4,
        190,
        202401,
        2024,
        2024,
        "winter",
        ["Very practical SQL content.", "Great database systems overview."],
    ),
    (
        5006,
        1006,
        ["Andrew Ng"],
        4.8,
        300,
        202403,
        2024,
        2024,
        "spring",
        ["Classic ML course.", "Strong math plus implementation."],
    ),
]

PROGRAMS = [
    (
        9001,
        "MS",
        "general",
        "Complete core courses plus depth and breadth requirements.",
        "https://example.edu/mscs/requirements",
        "/path/to/ms-general-sheet",
    ),
    (
        9002,
        "MS",
        "AI",
        "Take ML, NLP, and AI depth electives with implementation components.",
        "https://example.edu/mscs/ai-track",
        "/path/to/ms-ai-sheet",
    ),
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS courses (
    course_id INT PRIMARY KEY,
    max_units INT,
    title TEXT NOT NULL,
    grading TEXT,
    course_codes TEXT[],
    general_requirements TEXT[],
    min_units INT,
    description TEXT,
    average_hours_spent FLOAT,
    foundations_requirements TEXT[],
    significant_implementation_requirements BOOLEAN,
    breadth_requirement TEXT[],
    prerequisite_course_codes TEXT[]
);

CREATE TABLE IF NOT EXISTS offerings (
    course_id INT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    days TEXT[],
    start_time TEXT,
    end_time TEXT,
    instructor_names TEXT[],
    season TEXT
);

CREATE TABLE IF NOT EXISTS ratings (
    rating_id INT PRIMARY KEY,
    course_id INT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    instructor_names TEXT[],
    average_rating FLOAT,
    num_ratings INT,
    term_id INT,
    start_year INT,
    end_year INT,
    season TEXT,
    reviews TEXT[]
);

CREATE TABLE IF NOT EXISTS programs (
    program_id INT PRIMARY KEY,
    level TEXT,
    specialization TEXT,
    sheet_requirements TEXT,
    sheet_url TEXT,
    sheet_path TEXT
);
"""


def _connect(cfg: DbConfig, database_override: str | None = None):
    return psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname=database_override or cfg.database,
    )


def ensure_database(cfg: DbConfig) -> None:
    conn = _connect(cfg, database_override="postgres")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (cfg.database,),
            )
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{cfg.database}"')
    finally:
        conn.close()


def reset_tables(cur) -> None:
    cur.execute("DROP TABLE IF EXISTS ratings")
    cur.execute("DROP TABLE IF EXISTS offerings")
    cur.execute("DROP TABLE IF EXISTS programs")
    cur.execute("DROP TABLE IF EXISTS courses")


def insert_rows(cur, table: str, columns: Iterable[str], rows: list[tuple]) -> None:
    execute_values(
        cur,
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s",
        rows,
    )


def seed(cfg: DbConfig, reset: bool) -> None:
    ensure_database(cfg)
    conn = _connect(cfg)
    try:
        with conn, conn.cursor() as cur:
            if reset:
                reset_tables(cur)
            cur.execute(SCHEMA_SQL)

            cur.execute("TRUNCATE TABLE ratings, offerings, programs, courses RESTART IDENTITY")
            insert_rows(
                cur,
                "courses",
                [
                    "course_id",
                    "max_units",
                    "title",
                    "grading",
                    "course_codes",
                    "general_requirements",
                    "min_units",
                    "description",
                    "average_hours_spent",
                    "foundations_requirements",
                    "significant_implementation_requirements",
                    "breadth_requirement",
                    "prerequisite_course_codes",
                ],
                COURSES,
            )
            insert_rows(
                cur,
                "offerings",
                ["course_id", "days", "start_time", "end_time", "instructor_names", "season"],
                OFFERINGS,
            )
            insert_rows(
                cur,
                "ratings",
                [
                    "rating_id",
                    "course_id",
                    "instructor_names",
                    "average_rating",
                    "num_ratings",
                    "term_id",
                    "start_year",
                    "end_year",
                    "season",
                    "reviews",
                ],
                RATINGS,
            )
            insert_rows(
                cur,
                "programs",
                ["program_id", "level", "specialization", "sheet_requirements", "sheet_url", "sheet_path"],
                PROGRAMS,
            )
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Seed local course_assistant Postgres database.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--user", default="select_user")
    parser.add_argument("--password", default="select_user")
    parser.add_argument("--database", default="course_assistant")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate tables before seeding.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = DbConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )
    seed(cfg, reset=args.reset)
    print("Seeded local course_assistant database successfully.")


if __name__ == "__main__":
    main()
