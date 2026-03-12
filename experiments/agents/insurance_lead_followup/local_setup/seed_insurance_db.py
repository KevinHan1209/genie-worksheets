#!/usr/bin/env python3
"""Create and seed a minimal local `insurance` Postgres database.

Run with --reset to truncate all tables and re-insert the default lead.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import psycopg2


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


DEFAULT_LEAD = {
    "lead_id": 1,
    "target_name": "Kevin",
    "phone": "(555) 867-5309",
    "email": "kevin.han@example.com",
    "status": "new",
    "do_not_call": False,
    "callback_time_text": None,
}

DEFAULT_QUOTE = {
    "quote_id": 1,
    "lead_id": 1,
    "property_address": "742 Evergreen Terrace, Springfield, IL",
    "property_type": "single-family home",
    "coverage_start": "April 15",
    "estimated_premium": None,
}

DEFAULT_UNDERWRITING = {
    "underwriting_id": 1,
    "lead_id": 1,
    "roof_age_years": None,
    "claims_past_five_years": None,
    "claims_details": None,
    "disposition": None,
    "notes": None,
}


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
            CREATE TABLE IF NOT EXISTS insurance_leads (
                lead_id SERIAL PRIMARY KEY,
                target_name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                do_not_call BOOLEAN NOT NULL DEFAULT FALSE,
                callback_time_text TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS insurance_quotes (
                quote_id SERIAL PRIMARY KEY,
                lead_id INT NOT NULL REFERENCES insurance_leads(lead_id),
                property_address TEXT,
                property_type TEXT,
                coverage_start TEXT,
                estimated_premium TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS insurance_underwriting (
                underwriting_id SERIAL PRIMARY KEY,
                lead_id INT NOT NULL REFERENCES insurance_leads(lead_id),
                roof_age_years TEXT,
                claims_past_five_years TEXT,
                claims_details TEXT,
                disposition TEXT,
                notes TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS insurance_appointments (
                appointment_id SERIAL PRIMARY KEY,
                lead_id INT NOT NULL,
                slot_datetime_text TEXT,
                agent_name TEXT,
                booking_status TEXT NOT NULL DEFAULT 'pending'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS insurance_call_log (
                call_id SERIAL PRIMARY KEY,
                lead_id INT NOT NULL,
                contact_outcome TEXT,
                disposition TEXT,
                interested_in_consultation BOOLEAN,
                appointment_id INT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )


def seed_data(conn, reset: bool = False):
    with conn.cursor() as cur:
        if reset:
            cur.execute("TRUNCATE TABLE insurance_call_log CASCADE;")
            cur.execute("TRUNCATE TABLE insurance_appointments CASCADE;")
            cur.execute("TRUNCATE TABLE insurance_underwriting CASCADE;")
            cur.execute("TRUNCATE TABLE insurance_quotes CASCADE;")
            cur.execute("TRUNCATE TABLE insurance_leads CASCADE;")

        lead = DEFAULT_LEAD
        cur.execute(
            """
            INSERT INTO insurance_leads
                (lead_id, target_name, phone, email, status, do_not_call, callback_time_text)
            VALUES (%(lead_id)s, %(target_name)s, %(phone)s, %(email)s,
                    %(status)s, %(do_not_call)s, %(callback_time_text)s)
            ON CONFLICT (lead_id) DO UPDATE SET
                target_name = EXCLUDED.target_name,
                phone = EXCLUDED.phone,
                email = EXCLUDED.email,
                status = EXCLUDED.status,
                do_not_call = EXCLUDED.do_not_call,
                callback_time_text = EXCLUDED.callback_time_text;
            """,
            lead,
        )

        quote = DEFAULT_QUOTE
        cur.execute(
            """
            INSERT INTO insurance_quotes
                (quote_id, lead_id, property_address, property_type,
                 coverage_start, estimated_premium)
            VALUES (%(quote_id)s, %(lead_id)s, %(property_address)s,
                    %(property_type)s, %(coverage_start)s, %(estimated_premium)s)
            ON CONFLICT (quote_id) DO UPDATE SET
                lead_id = EXCLUDED.lead_id,
                property_address = EXCLUDED.property_address,
                property_type = EXCLUDED.property_type,
                coverage_start = EXCLUDED.coverage_start,
                estimated_premium = EXCLUDED.estimated_premium;
            """,
            quote,
        )

        uw = DEFAULT_UNDERWRITING
        cur.execute(
            """
            INSERT INTO insurance_underwriting
                (underwriting_id, lead_id, roof_age_years, claims_past_five_years,
                 claims_details, disposition, notes)
            VALUES (%(underwriting_id)s, %(lead_id)s, %(roof_age_years)s,
                    %(claims_past_five_years)s, %(claims_details)s,
                    %(disposition)s, %(notes)s)
            ON CONFLICT (underwriting_id) DO UPDATE SET
                lead_id = EXCLUDED.lead_id,
                roof_age_years = EXCLUDED.roof_age_years,
                claims_past_five_years = EXCLUDED.claims_past_five_years,
                claims_details = EXCLUDED.claims_details,
                disposition = EXCLUDED.disposition,
                notes = EXCLUDED.notes;
            """,
            uw,
        )

        cur.execute(
            "SELECT setval('insurance_leads_lead_id_seq', "
            "(SELECT COALESCE(MAX(lead_id), 0) FROM insurance_leads));"
        )
        cur.execute(
            "SELECT setval('insurance_quotes_quote_id_seq', "
            "(SELECT COALESCE(MAX(quote_id), 0) FROM insurance_quotes));"
        )
        cur.execute(
            "SELECT setval('insurance_underwriting_underwriting_id_seq', "
            "(SELECT COALESCE(MAX(underwriting_id), 0) FROM insurance_underwriting));"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed / reset local insurance lead DB."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="select_user")
    parser.add_argument("--password", default="select_user")
    parser.add_argument("--database", default="insurance")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate all tables and re-insert the default seed data.",
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
    conn = connect(cfg)
    try:
        ensure_schema(conn)
        seed_data(conn, reset=args.reset)
        conn.commit()
        print(
            f"{'Reset and seeded' if args.reset else 'Seeded'} "
            f"insurance DB ({cfg.database})."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
