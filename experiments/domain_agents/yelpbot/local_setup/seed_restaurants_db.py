#!/usr/bin/env python3
"""Create and seed a minimal local `restaurants` Postgres database."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import psycopg2
from psycopg2.extras import execute_values


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


RESTAURANTS = [
    (
        1,
        "Mathilde French Bistro",
        ["french", "desserts"],
        "expensive",
        4.5,
        345,
        "Onion Soup Gratinee; Duck Confit; Beef Bourguignon Ravioli",
        "(415) 546-6128",
        "Great atmosphere. Friendly service. Excellent French classics.",
        "Mon-Fri 17:30-21:30",
        "San Francisco",
    ),
    (
        2,
        "Sakura Sushi",
        ["japanese", "sushi"],
        "moderate",
        4.6,
        210,
        "Omakase; Sashimi Platter; Dragon Roll",
        "(650) 555-0123",
        "Fresh fish and quick service. Cozy setting.",
        "Daily 11:30-22:00",
        "Palo Alto",
    ),
    (
        3,
        "Gui's Vegan House",
        ["vegan", "healthy"],
        "moderate",
        4.8,
        512,
        "Vegan Ramen; Tofu Bowl; Matcha Cheesecake",
        "(408) 555-0188",
        "High quality food and thoughtful options for dietary needs.",
        "Daily 10:00-21:00",
        "Sunnyvale",
    ),
]


def connect(cfg: DbConfig):
    return psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname=cfg.database,
    )


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS restaurants (
                _id INT PRIMARY KEY,
                name TEXT NOT NULL,
                cuisines TEXT[] NOT NULL,
                price TEXT NOT NULL,
                rating NUMERIC(2,1) NOT NULL,
                num_reviews INT NOT NULL,
                popular_dishes TEXT,
                phone_number TEXT,
                reviews TEXT,
                opening_hours TEXT,
                location TEXT NOT NULL
            );
            """
        )


def seed_data(conn, reset: bool):
    with conn.cursor() as cur:
        if reset:
            cur.execute("TRUNCATE TABLE restaurants;")

        execute_values(
            cur,
            """
            INSERT INTO restaurants (
                _id, name, cuisines, price, rating, num_reviews,
                popular_dishes, phone_number, reviews, opening_hours, location
            )
            VALUES %s
            ON CONFLICT (_id) DO UPDATE SET
                name = EXCLUDED.name,
                cuisines = EXCLUDED.cuisines,
                price = EXCLUDED.price,
                rating = EXCLUDED.rating,
                num_reviews = EXCLUDED.num_reviews,
                popular_dishes = EXCLUDED.popular_dishes,
                phone_number = EXCLUDED.phone_number,
                reviews = EXCLUDED.reviews,
                opening_hours = EXCLUDED.opening_hours,
                location = EXCLUDED.location;
            """,
            RESTAURANTS,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Seed local yelp restaurants DB.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="select_user")
    parser.add_argument("--password", default="select_user")
    parser.add_argument("--database", default="restaurants")
    parser.add_argument("--reset", action="store_true")
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
    conn = connect(cfg)
    try:
        ensure_schema(conn)
        seed_data(conn, reset=args.reset)
        conn.commit()
        print("Seed complete for restaurants DB.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
