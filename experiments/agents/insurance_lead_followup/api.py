import datetime
import json
import os
import re
from uuid import uuid4

import psycopg2
from psycopg2 import sql


AVAILABLE_SLOTS = [
    {"datetime": "Thursday 2:00 PM", "agent_name": "James Rivera"},
    {"datetime": "Friday 2:00 PM", "agent_name": "Monica Chen"},
    {"datetime": "Friday 10:00 AM", "agent_name": "James Rivera"},
]

current_dir = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CONTROL_STATE_PATH = os.path.join(
    current_dir, "local_setup", "control_db_state.json"
)
TABLES_TO_CLONE = [
    "insurance_leads",
    "insurance_quotes",
    "insurance_underwriting",
    "insurance_appointments",
    "insurance_call_log",
]


def _get_field_value(field):
    """Extract the raw value from a GenieField or GenieValue wrapper."""
    if hasattr(field, "value"):
        v = field.value
        if hasattr(v, "value"):
            return v.value
        return v
    return field


def _resolve_main_worksheet(arg):
    """Resolve a `Main` worksheet instance from action argument shapes.

    Runtime action calls may pass:
    - the worksheet instance itself
    - a field wrapper whose `.parent` is the worksheet
    - a wrapper whose `.value` is the worksheet
    """
    if arg is None:
        return None

    # Direct worksheet instance.
    if hasattr(arg, "_ordered_attributes"):
        return arg

    # Field wrapper shape.
    if hasattr(arg, "parent") and hasattr(arg, "slottype"):
        raw_value = getattr(arg, "value", None)
        if hasattr(raw_value, "_ordered_attributes"):
            return raw_value
        parent = getattr(arg, "parent", None)
        if hasattr(parent, "_ordered_attributes"):
            return parent

    # Generic wrapper shape.
    raw_value = getattr(arg, "value", None)
    if hasattr(raw_value, "_ordered_attributes"):
        return raw_value

    return None


def _string_or_unknown(value):
    return str(value) if value not in (None, "") else "not provided"


def _parse_lead_id(lead_id, default=1):
    value = _get_field_value(lead_id) if lead_id is not None else default
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _sanitize_identifier(value, fallback="insurance_chat"):
    text = re.sub(r"[^a-zA-Z0-9_]", "_", str(value or ""))
    text = re.sub(r"_+", "_", text).strip("_").lower()
    if not text:
        text = fallback
    if not text[0].isalpha():
        text = f"n_{text}"
    return text[:50]


def _get_control_schema():
    return os.getenv("INSURANCE_CONTROL_SCHEMA", "insurance_control")


def _active_schema():
    return os.getenv("INSURANCE_DB_SCHEMA")


def set_active_db_schema(schema_name=None, **kwargs):
    """Set process-level DB schema for all connections in this chat session."""
    if schema_name:
        schema = _sanitize_identifier(schema_name, fallback="insurance_chat")
        os.environ["INSURANCE_DB_SCHEMA"] = schema
        os.environ["PGOPTIONS"] = f"-c search_path={schema},public"
        return {"status": "success", "active_schema": schema}
    os.environ.pop("INSURANCE_DB_SCHEMA", None)
    os.environ.pop("PGOPTIONS", None)
    return {"status": "success", "active_schema": None}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db_config():
    return {
        "host": os.getenv("INSURANCE_DB_HOST", "127.0.0.1"),
        "port": os.getenv("INSURANCE_DB_PORT", "5432"),
        "user": os.getenv("INSURANCE_DB_USER", "select_user"),
        "password": os.getenv("INSURANCE_DB_PASSWORD", "select_user"),
        "database": os.getenv("INSURANCE_DB_NAME", "insurance"),
    }


def _get_db_connection():
    cfg = _get_db_config()
    connect_kwargs = {
        "host": cfg["host"],
        "port": int(cfg["port"]),
        "user": cfg["user"],
        "password": cfg["password"],
        "dbname": cfg["database"],
    }
    schema = _active_schema()
    if schema:
        connect_kwargs["options"] = f"-c search_path={schema},public"
    return psycopg2.connect(**connect_kwargs)


def _get_admin_connection():
    cfg = _get_db_config()
    return psycopg2.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["database"],
    )


def _ensure_schema_objects(schema_name):
    from insurance_lead_followup.local_setup.seed_insurance_db import ensure_schema

    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(
                    sql.Identifier(schema_name)
                )
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SET search_path TO {}, public;").format(
                    sql.Identifier(schema_name)
                )
            )
        ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _copy_table_data(source_schema, target_schema, table_name):
    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DELETE FROM {}.{};").format(
                    sql.Identifier(target_schema),
                    sql.Identifier(table_name),
                )
            )
            cur.execute(
                sql.SQL("INSERT INTO {}.{} SELECT * FROM {}.{};").format(
                    sql.Identifier(target_schema),
                    sql.Identifier(table_name),
                    sql.Identifier(source_schema),
                    sql.Identifier(table_name),
                )
            )
        conn.commit()
    finally:
        conn.close()


def _table_has_rows(schema_name, table_name):
    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{} LIMIT 1);").format(
                    sql.Identifier(schema_name), sql.Identifier(table_name)
                )
            )
            row = cur.fetchone()
            return bool(row and row[0])
    finally:
        conn.close()


def _reset_schema_sequences(schema_name):
    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            sequence_targets = [
                ("insurance_leads", "lead_id"),
                ("insurance_quotes", "quote_id"),
                ("insurance_underwriting", "underwriting_id"),
                ("insurance_appointments", "appointment_id"),
                ("insurance_call_log", "call_id"),
            ]

            for table_name, pk_col in sequence_targets:
                cur.execute(
                    sql.SQL("SELECT MAX({}) FROM {}.{};").format(
                        sql.Identifier(pk_col),
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                    )
                )
                max_id = cur.fetchone()[0]
                if max_id is None:
                    # For empty tables, initialize to 1 and mark as not called so
                    # the next nextval() returns 1.
                    cur.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, %s), %s, false);",
                        (f"{schema_name}.{table_name}", pk_col, 1),
                    )
                else:
                    cur.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, %s), %s, true);",
                        (f"{schema_name}.{table_name}", pk_col, int(max_id)),
                    )
        conn.commit()
    finally:
        conn.close()


def ensure_control_schema_seeded(**kwargs):
    """Ensure persistent control schema exists and has baseline rows."""
    control_schema = _get_control_schema()
    _ensure_schema_objects(control_schema)

    if _table_has_rows(control_schema, "insurance_leads"):
        return {"status": "success", "control_schema": control_schema, "seeded": False}

    # Bootstrap control state from current public schema once.
    for table_name in TABLES_TO_CLONE:
        _copy_table_data("public", control_schema, table_name)
    _reset_schema_sequences(control_schema)
    return {"status": "success", "control_schema": control_schema, "seeded": True}


def create_chat_schema_from_control(chat_id=None, **kwargs):
    """Create a per-chat schema cloned from the control schema."""
    ensure_control_schema_seeded()
    control_schema = _get_control_schema()
    prefix = os.getenv("INSURANCE_CHAT_SCHEMA_PREFIX", "insurance_chat")
    schema_name = _sanitize_identifier(f"{prefix}_{chat_id or uuid4().hex[:8]}")

    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(
                    sql.Identifier(schema_name)
                )
            )
            cur.execute(
                sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(schema_name))
            )
        conn.commit()
    finally:
        conn.close()

    _ensure_schema_objects(schema_name)
    for table_name in TABLES_TO_CLONE:
        _copy_table_data(control_schema, schema_name, table_name)
    _reset_schema_sequences(schema_name)

    return {
        "status": "success",
        "control_schema": control_schema,
        "chat_schema": schema_name,
    }


def drop_schema(schema_name, **kwargs):
    """Drop a schema and all objects."""
    if not schema_name:
        return {"status": "no_op", "reason": "missing_schema_name"}
    safe_name = _sanitize_identifier(schema_name)
    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(
                    sql.Identifier(safe_name)
                )
            )
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "dropped_schema": safe_name}


def promote_chat_schema_to_control(chat_schema, **kwargs):
    """Replace control schema data with a completed chat schema state."""
    if not chat_schema:
        return {"status": "no_op", "reason": "missing_chat_schema"}
    ensure_control_schema_seeded()
    control_schema = _get_control_schema()
    source = _sanitize_identifier(chat_schema)
    for table_name in TABLES_TO_CLONE:
        _copy_table_data(source, control_schema, table_name)
    _reset_schema_sequences(control_schema)
    return {
        "status": "success",
        "control_schema": control_schema,
        "source_chat_schema": source,
    }


def export_schema_state(schema_name, output_path, lead_id=1, **kwargs):
    """Export full schema state for inspection after a chat."""
    safe_schema = _sanitize_identifier(schema_name)
    lead_id_int = _parse_lead_id(lead_id, default=1)
    state = {
        "schema": safe_schema,
        "lead_id": lead_id_int,
        "captured_at": datetime.datetime.now().isoformat(),
    }

    conn = _get_admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT lead_id, target_name, phone, email, status, do_not_call, callback_time_text
                    FROM {}.insurance_leads WHERE lead_id = %s;
                    """
                ).format(sql.Identifier(safe_schema)),
                (lead_id_int,),
            )
            row = cur.fetchone()
            state["insurance_leads"] = (
                {
                    "lead_id": row[0],
                    "target_name": row[1],
                    "phone": row[2],
                    "email": row[3],
                    "status": row[4],
                    "do_not_call": row[5],
                    "callback_time_text": row[6],
                }
                if row
                else None
            )

            for table_name, columns in [
                (
                    "insurance_quotes",
                    [
                        "quote_id",
                        "lead_id",
                        "property_address",
                        "property_type",
                        "coverage_start",
                        "estimated_premium",
                    ],
                ),
                (
                    "insurance_underwriting",
                    [
                        "underwriting_id",
                        "lead_id",
                        "roof_age_years",
                        "claims_past_five_years",
                        "claims_details",
                        "disposition",
                        "notes",
                    ],
                ),
                (
                    "insurance_appointments",
                    [
                        "appointment_id",
                        "lead_id",
                        "slot_datetime_text",
                        "agent_name",
                        "booking_status",
                    ],
                ),
                (
                    "insurance_call_log",
                    [
                        "call_id",
                        "lead_id",
                        "contact_outcome",
                        "disposition",
                        "interested_in_consultation",
                        "appointment_id",
                        "created_at",
                    ],
                ),
            ]:
                cur.execute(
                    sql.SQL("SELECT * FROM {}.{} WHERE lead_id = %s ORDER BY 1;").format(
                        sql.Identifier(safe_schema), sql.Identifier(table_name)
                    ),
                    (lead_id_int,),
                )
                rows = cur.fetchall()
                state[table_name] = [
                    dict(zip(columns, [str(v) if isinstance(v, datetime.datetime) else v for v in r]))
                    for r in rows
                ]
    finally:
        conn.close()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return {"status": "success", "output_path": output_path, "schema": safe_schema}


# ---------------------------------------------------------------------------
# Disposition logic
# ---------------------------------------------------------------------------

def _compute_disposition(intake_ws):
    """Conservative underwriting disposition from intake data.

    - default toward 'qualified'
    - 'qualified_with_concerns' for mild risk indicators
    - 'not_viable' only for clear hard blockers
    """
    roof_age = _get_field_value(getattr(intake_ws, "roof_age_years", None))
    claims = _get_field_value(getattr(intake_ws, "claims_past_five_years", None))

    try:
        roof_years = int(roof_age) if roof_age is not None else 0
    except (ValueError, TypeError):
        roof_years = 0

    if claims == "multiple" and roof_years > 25:
        return "not_viable"

    if claims in ("one", "multiple") or roof_years > 20:
        return "qualified_with_concerns"

    return "qualified"


# ---------------------------------------------------------------------------
# Public APIs (registered with GenieRuntime)
# ---------------------------------------------------------------------------

def check_availability(time_preference, **kwargs):
    """Return available consultation slots filtered by time preference."""
    pref = _get_field_value(time_preference) if time_preference else None

    slots = AVAILABLE_SLOTS
    if pref == "morning":
        slots = [s for s in slots if "AM" in s["datetime"]]
    elif pref == "afternoon":
        slots = [s for s in slots if "PM" in s["datetime"]]

    return {
        "status": "success",
        "slots": slots,
    }


def _parse_slot_agent(slot_text):
    """Split 'Thursday 2:00 PM with James Rivera' into (datetime, agent)."""
    if slot_text and " with " in str(slot_text):
        parts = str(slot_text).split(" with ", 1)
        return parts[0].strip(), parts[1].strip()
    return str(slot_text) if slot_text else "unknown", None


def _persist_booking(slot_text, agent_name, lead_id=1):
    """Write an appointment row to the DB and return the generated ID."""
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO insurance_appointments
                        (lead_id, slot_datetime_text, agent_name, booking_status)
                    VALUES (%s, %s, %s, 'confirmed')
                    RETURNING appointment_id;
                    """,
                    (lead_id, slot_text, agent_name),
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def snapshot_control_db_state(lead_id=1, snapshot_path=None, **kwargs):
    """Persist a JSON snapshot ("image") of the control DB state for one lead."""
    lead_id_int = _parse_lead_id(lead_id, default=1)
    path = snapshot_path or os.getenv(
        "INSURANCE_CONTROL_STATE_PATH", DEFAULT_CONTROL_STATE_PATH
    )

    state = {"lead_id": lead_id_int, "captured_at": datetime.datetime.now().isoformat()}
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lead_id, target_name, phone, email, status, do_not_call, callback_time_text
                    FROM insurance_leads
                    WHERE lead_id = %s;
                    """,
                    (lead_id_int,),
                )
                row = cur.fetchone()
                state["insurance_leads"] = (
                    {
                        "lead_id": row[0],
                        "target_name": row[1],
                        "phone": row[2],
                        "email": row[3],
                        "status": row[4],
                        "do_not_call": row[5],
                        "callback_time_text": row[6],
                    }
                    if row
                    else None
                )

                cur.execute(
                    """
                    SELECT quote_id, lead_id, property_address, property_type, coverage_start, estimated_premium
                    FROM insurance_quotes
                    WHERE lead_id = %s
                    ORDER BY quote_id;
                    """,
                    (lead_id_int,),
                )
                quote_rows = cur.fetchall()
                state["insurance_quotes"] = [
                    {
                        "quote_id": r[0],
                        "lead_id": r[1],
                        "property_address": r[2],
                        "property_type": r[3],
                        "coverage_start": r[4],
                        "estimated_premium": r[5],
                    }
                    for r in quote_rows
                ]

                cur.execute(
                    """
                    SELECT underwriting_id, lead_id, roof_age_years, claims_past_five_years,
                           claims_details, disposition, notes
                    FROM insurance_underwriting
                    WHERE lead_id = %s
                    ORDER BY underwriting_id;
                    """,
                    (lead_id_int,),
                )
                uw_rows = cur.fetchall()
                state["insurance_underwriting"] = [
                    {
                        "underwriting_id": r[0],
                        "lead_id": r[1],
                        "roof_age_years": r[2],
                        "claims_past_five_years": r[3],
                        "claims_details": r[4],
                        "disposition": r[5],
                        "notes": r[6],
                    }
                    for r in uw_rows
                ]
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "error", "message": f"snapshot failed: {exc}"}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return {"status": "success", "snapshot_path": path, "lead_id": lead_id_int}


def restore_control_db_state(snapshot_path=None, **kwargs):
    """Restore lead/quote/underwriting tables from the control DB snapshot JSON."""
    path = snapshot_path or os.getenv(
        "INSURANCE_CONTROL_STATE_PATH", DEFAULT_CONTROL_STATE_PATH
    )
    if not os.path.exists(path):
        return {"status": "not_found", "snapshot_path": path}

    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)

    lead = state.get("insurance_leads")
    quotes = state.get("insurance_quotes", [])
    underwriting = state.get("insurance_underwriting", [])

    if not lead:
        return {"status": "error", "message": "snapshot missing lead row"}

    lead_id_int = _parse_lead_id(lead.get("lead_id"), default=1)

    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO insurance_leads
                        (lead_id, target_name, phone, email, status, do_not_call, callback_time_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (lead_id) DO UPDATE SET
                        target_name = EXCLUDED.target_name,
                        phone = EXCLUDED.phone,
                        email = EXCLUDED.email,
                        status = EXCLUDED.status,
                        do_not_call = EXCLUDED.do_not_call,
                        callback_time_text = EXCLUDED.callback_time_text;
                    """,
                    (
                        lead_id_int,
                        lead.get("target_name"),
                        lead.get("phone"),
                        lead.get("email"),
                        lead.get("status"),
                        bool(lead.get("do_not_call")),
                        lead.get("callback_time_text"),
                    ),
                )

                for q in quotes:
                    cur.execute(
                        """
                        INSERT INTO insurance_quotes
                            (quote_id, lead_id, property_address, property_type, coverage_start, estimated_premium)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (quote_id) DO UPDATE SET
                            lead_id = EXCLUDED.lead_id,
                            property_address = EXCLUDED.property_address,
                            property_type = EXCLUDED.property_type,
                            coverage_start = EXCLUDED.coverage_start,
                            estimated_premium = EXCLUDED.estimated_premium;
                        """,
                        (
                            _parse_lead_id(q.get("quote_id"), default=1),
                            lead_id_int,
                            q.get("property_address"),
                            q.get("property_type"),
                            q.get("coverage_start"),
                            q.get("estimated_premium"),
                        ),
                    )

                for uw in underwriting:
                    cur.execute(
                        """
                        INSERT INTO insurance_underwriting
                            (underwriting_id, lead_id, roof_age_years, claims_past_five_years,
                             claims_details, disposition, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (underwriting_id) DO UPDATE SET
                            lead_id = EXCLUDED.lead_id,
                            roof_age_years = EXCLUDED.roof_age_years,
                            claims_past_five_years = EXCLUDED.claims_past_five_years,
                            claims_details = EXCLUDED.claims_details,
                            disposition = EXCLUDED.disposition,
                            notes = EXCLUDED.notes;
                        """,
                        (
                            _parse_lead_id(uw.get("underwriting_id"), default=1),
                            lead_id_int,
                            uw.get("roof_age_years"),
                            uw.get("claims_past_five_years"),
                            uw.get("claims_details"),
                            uw.get("disposition"),
                            uw.get("notes"),
                        ),
                    )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "error", "message": f"restore failed: {exc}"}

    return {"status": "success", "snapshot_path": path, "lead_id": lead_id_int}


def book_appointment(selected_slot, **kwargs):
    """Book the selected consultation slot, persist to DB, and return confirmation."""
    slot_text = _get_field_value(selected_slot) if selected_slot else "unknown"
    slot_dt, agent_name = _parse_slot_agent(slot_text)

    appointment_id = _persist_booking(slot_dt, agent_name)

    return {
        "status": "success",
        "appointment_id": appointment_id or str(uuid4()),
        "slot": slot_text,
        "booking_status": "confirmed",
    }


# ---------------------------------------------------------------------------
# Terminal outcome persistence helpers
# ---------------------------------------------------------------------------

def _determine_contact_outcome(lc):
    """Derive contact outcome string from LeadContact worksheet."""
    is_right = _get_field_value(getattr(lc, "is_right_person", None)) if lc else None
    permission = _get_field_value(getattr(lc, "permission_to_continue", None)) if lc else None
    callback = _get_field_value(getattr(lc, "callback_requested", None)) if lc else None
    dnc = _get_field_value(getattr(lc, "do_not_call", None)) if lc else None

    if is_right is not True:
        return "wrong_person"
    if permission is not True:
        if dnc is True:
            return "do_not_call"
        if callback is True:
            return "callback_requested"
        return "refused"
    return "proceeded"


def _persist_lead_updates(contact_outcome, disposition, callback_text, dnc, lead_id=1):
    """Update the insurance_leads row with call outcome side-effects."""
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                status_map = {
                    "proceeded": "contacted",
                    "wrong_person": "wrong_person",
                    "callback_requested": "callback",
                    "do_not_call": "do_not_call",
                    "refused": "refused",
                }
                new_status = status_map.get(contact_outcome, "contacted")
                cur.execute(
                    """
                    UPDATE insurance_leads
                    SET status = %s,
                        do_not_call = %s,
                        callback_time_text = %s
                    WHERE lead_id = %s;
                    """,
                    (new_status, bool(dnc), callback_text, lead_id),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _persist_underwriting(ui, disposition, lead_id=1):
    """Update the insurance_underwriting row with collected intake data."""
    try:
        conn = _get_db_connection()
        try:
            roof_age = _get_field_value(getattr(ui, "roof_age_years", None))
            claims = _get_field_value(getattr(ui, "claims_past_five_years", None))
            details = _get_field_value(getattr(ui, "claims_details", None))
            notes = _get_field_value(getattr(ui, "notes", None))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE insurance_underwriting
                    SET roof_age_years = %s,
                        claims_past_five_years = %s,
                        claims_details = %s,
                        disposition = %s,
                        notes = %s
                    WHERE lead_id = %s;
                    """,
                    (roof_age, claims, details, disposition, notes, lead_id),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _persist_quote_corrections(vq, lead_id=1):
    """Update insurance_quotes with verified/corrected quote values."""
    try:
        conn = _get_db_connection()
        try:
            property_address = _get_field_value(getattr(vq, "property_address", None))
            property_type = _get_field_value(getattr(vq, "property_type", None))
            coverage_start = _get_field_value(getattr(vq, "coverage_start", None))
            updates = []
            params = []

            if property_address:
                updates.append("property_address = %s")
                params.append(property_address)
            if property_type:
                updates.append("property_type = %s")
                params.append(property_type)
            if coverage_start:
                updates.append("coverage_start = %s")
                params.append(coverage_start)

            if updates:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE insurance_quotes SET {', '.join(updates)} "
                        f"WHERE lead_id = %s;",
                        params + [lead_id],
                    )
                    conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def hydrate_verify_quote(verify_quote, lead_id=1, **kwargs):
    """Populate VerifyQuote worksheet with on-file quote details.

    This is intended to run when `main.verify_quote` is initialized so the
    verification flow can ask confirmation questions against real values.
    """
    vq = verify_quote.value if hasattr(verify_quote, "value") else verify_quote
    lead_id_value = _get_field_value(lead_id) if lead_id is not None else 1

    try:
        lead_id_int = int(lead_id_value) if lead_id_value is not None else 1
    except (ValueError, TypeError):
        lead_id_int = 1

    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT q.property_address, q.property_type, q.coverage_start, l.target_name
                    FROM insurance_quotes q
                    JOIN insurance_leads l ON q.lead_id = l.lead_id
                    WHERE l.lead_id = %s;
                    """,
                    (lead_id_int,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        row = None

    if not row or vq is None:
        return {"status": "not_found", "lead_id": lead_id_int}

    property_address, property_type, coverage_start, target_name = row
    vq.property_address = str(property_address) if property_address is not None else None
    vq.property_type = str(property_type) if property_type is not None else None
    vq.coverage_start = str(coverage_start) if coverage_start is not None else None
    vq.target_name = str(target_name) if target_name is not None else None

    return {
        "status": "success",
        "lead_id": lead_id_int,
        "property_address": vq.property_address,
        "property_type": vq.property_type,
        "coverage_start": vq.coverage_start,
        "target_name": vq.target_name,
    }


def _persist_call_log(contact_outcome, disposition, interested, appointment_id, lead_id=1):
    """Insert a row into insurance_call_log summarising the call."""
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO insurance_call_log
                        (lead_id, contact_outcome, disposition,
                         interested_in_consultation, appointment_id)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (lead_id, contact_outcome, disposition, interested, appointment_id),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def process_lead_outcome(
    lead_contact,
    verify_quote,
    underwriting_intake,
    schedule_consultation,
    **kwargs,
):
    """Terminal backend API for Main — persists the full call outcome to DB."""
    lead_id = _parse_lead_id(kwargs.get("lead_id"), default=1)
    lc = lead_contact.value if hasattr(lead_contact, "value") else lead_contact
    vq = verify_quote.value if hasattr(verify_quote, "value") else verify_quote
    ui = underwriting_intake.value if hasattr(underwriting_intake, "value") else underwriting_intake
    sc = schedule_consultation.value if hasattr(schedule_consultation, "value") else schedule_consultation

    contact_outcome = _determine_contact_outcome(lc)

    disposition = None
    if ui is not None:
        disposition = _compute_disposition(ui)

    interested = _get_field_value(getattr(sc, "interested_in_consultation", None)) if sc else None
    slot = _get_field_value(getattr(sc, "selected_slot", None)) if sc else None

    callback_text = _get_field_value(getattr(lc, "callback_time_text", None)) if lc else None
    dnc = _get_field_value(getattr(lc, "do_not_call", None)) if lc else None

    _persist_lead_updates(contact_outcome, disposition, callback_text, dnc, lead_id=lead_id)

    if vq is not None:
        _persist_quote_corrections(vq, lead_id=lead_id)

    if ui is not None:
        _persist_underwriting(ui, disposition, lead_id=lead_id)

    booking_confirmed = _get_field_value(getattr(sc, "booking_confirmed", None)) if sc else None
    booking_result = None
    appointment_id = None
    if interested is True and booking_confirmed is True and slot:
        booking_result = book_appointment(slot)
        appointment_id = booking_result.get("appointment_id")

    _persist_call_log(contact_outcome, disposition, interested, appointment_id, lead_id=lead_id)

    return {
        "status": "success",
        "params": {
            "contact_outcome": contact_outcome,
            "disposition": disposition,
            "interested_in_consultation": interested,
            "selected_slot": slot,
        },
        "response": {
            "outcome_id": str(uuid4()),
            "contact_outcome": contact_outcome,
            "disposition": disposition,
            "appointment": booking_result,
            "timestamp": datetime.datetime.now().isoformat(),
        },
    }


def sync_lead_contact(lead_contact, lead_id=1, **kwargs):
    """Persist LeadContact phase as soon as values are collected."""
    lead_id_int = _parse_lead_id(lead_id, default=1)
    lc = lead_contact.value if hasattr(lead_contact, "value") else lead_contact
    if lc is None:
        return {"status": "no_op", "reason": "lead_contact_missing"}

    contact_outcome = _determine_contact_outcome(lc)
    callback_text = _get_field_value(getattr(lc, "callback_time_text", None))
    dnc = _get_field_value(getattr(lc, "do_not_call", None))
    _persist_lead_updates(contact_outcome, None, callback_text, dnc, lead_id=lead_id_int)
    return {"status": "success", "lead_id": lead_id_int, "contact_outcome": contact_outcome}


def sync_verify_quote(verify_quote, lead_id=1, **kwargs):
    """Persist quote corrections once verification is complete."""
    lead_id_int = _parse_lead_id(lead_id, default=1)
    vq = verify_quote.value if hasattr(verify_quote, "value") else verify_quote
    if vq is None:
        return {"status": "no_op", "reason": "verify_quote_missing"}
    _persist_quote_corrections(vq, lead_id=lead_id_int)
    return {"status": "success", "lead_id": lead_id_int}


def sync_underwriting_intake(underwriting_intake, lead_id=1, **kwargs):
    """Persist underwriting intake once required fields are complete."""
    lead_id_int = _parse_lead_id(lead_id, default=1)
    ui = (
        underwriting_intake.value
        if hasattr(underwriting_intake, "value")
        else underwriting_intake
    )
    if ui is None:
        return {"status": "no_op", "reason": "underwriting_intake_missing"}
    disposition = _compute_disposition(ui)
    _persist_underwriting(ui, disposition, lead_id=lead_id_int)
    return {"status": "success", "lead_id": lead_id_int, "disposition": disposition}


def sync_schedule_consultation(schedule_consultation, lead_id=1, **kwargs):
    """Persist consultation intent and optionally book the selected slot."""
    lead_id_int = _parse_lead_id(lead_id, default=1)
    sc = (
        schedule_consultation.value
        if hasattr(schedule_consultation, "value")
        else schedule_consultation
    )
    if sc is None:
        return {"status": "no_op", "reason": "schedule_consultation_missing"}

    interested = _get_field_value(getattr(sc, "interested_in_consultation", None))
    booking_confirmed = _get_field_value(getattr(sc, "booking_confirmed", None))
    slot = _get_field_value(getattr(sc, "selected_slot", None))
    booking_result = None
    if interested is True and booking_confirmed is True and slot:
        booking_result = book_appointment(slot)
    return {
        "status": "success",
        "lead_id": lead_id_int,
        "interested_in_consultation": interested,
        "booking_confirmed": booking_confirmed,
        "appointment": booking_result,
    }


def finalize_confirmed_main(main, **kwargs):
    """Commit the full call outcome after final user confirmation."""
    main_obj = _resolve_main_worksheet(main)
    if main_obj is None:
        return {"status": "no_op", "reason": "main_missing"}

    already_done = _get_field_value(getattr(main_obj, "finalization_done", None))
    if already_done is True:
        return {"status": "no_op", "reason": "already_finalized"}

    confirm = _get_field_value(getattr(main_obj, "final_confirm", None))
    if confirm is not True:
        return {"status": "no_op", "reason": "final_confirm_not_set"}

    lead_id = _parse_lead_id(getattr(main_obj, "lead_id", None), default=1)
    result = process_lead_outcome(
        getattr(main_obj, "lead_contact", None),
        getattr(main_obj, "verify_quote", None),
        getattr(main_obj, "underwriting_intake", None),
        getattr(main_obj, "schedule_consultation", None),
        lead_id=lead_id,
    )
    if result.get("status") == "success":
        main_obj.finalization_done = True
    return result


def build_confirmation_summary(main, **kwargs):
    """Build a user-facing summary of collected and confirmed call details."""
    main_obj = _resolve_main_worksheet(main)
    if main_obj is None:
        return "Thanks for confirming. I wasn't able to read back the details."

    lc = _get_field_value(getattr(main_obj, "lead_contact", None))
    vq = _get_field_value(getattr(main_obj, "verify_quote", None))
    ui = _get_field_value(getattr(main_obj, "underwriting_intake", None))
    sc = _get_field_value(getattr(main_obj, "schedule_consultation", None))

    lines = ["Thanks for confirming. Here is a summary of what we collected:"]

    if lc is not None:
        is_right = _get_field_value(getattr(lc, "is_right_person", None))
        permission = _get_field_value(getattr(lc, "permission_to_continue", None))
        callback = _get_field_value(getattr(lc, "callback_requested", None))
        callback_time = _get_field_value(getattr(lc, "callback_time_text", None))
        dnc = _get_field_value(getattr(lc, "do_not_call", None))
        lines.append("- Contact:")
        lines.append(f"  - right person: {_string_or_unknown(is_right)}")
        lines.append(f"  - permission to continue: {_string_or_unknown(permission)}")
        if callback is not None:
            lines.append(f"  - callback requested: {_string_or_unknown(callback)}")
        if callback_time:
            lines.append(f"  - callback time: {callback_time}")
        if dnc is not None:
            lines.append(f"  - do not call: {_string_or_unknown(dnc)}")

    if vq is not None:
        address = _get_field_value(getattr(vq, "property_address", None))
        ptype = _get_field_value(getattr(vq, "property_type", None))
        start = _get_field_value(getattr(vq, "coverage_start", None))
        lines.append("- Verified quote details:")
        lines.append(f"  - property address: {_string_or_unknown(address)}")
        lines.append(f"  - property type: {_string_or_unknown(ptype)}")
        lines.append(f"  - coverage start: {_string_or_unknown(start)}")

    if ui is not None:
        roof_age = _get_field_value(getattr(ui, "roof_age_years", None))
        claims = _get_field_value(getattr(ui, "claims_past_five_years", None))
        claims_details = _get_field_value(getattr(ui, "claims_details", None))
        disposition = _compute_disposition(ui)
        lines.append("- Underwriting intake:")
        lines.append(f"  - roof age: {_string_or_unknown(roof_age)}")
        lines.append(f"  - claims in last 5 years: {_string_or_unknown(claims)}")
        if claims_details not in (None, ""):
            lines.append(f"  - claims details: {claims_details}")
        lines.append(f"  - disposition: {_string_or_unknown(disposition)}")

    if sc is not None:
        interested = _get_field_value(getattr(sc, "interested_in_consultation", None))
        time_pref = _get_field_value(getattr(sc, "time_preference", None))
        slot = _get_field_value(getattr(sc, "selected_slot", None))
        booking_confirmed = _get_field_value(getattr(sc, "booking_confirmed", None))
        lines.append("- Consultation:")
        lines.append(f"  - interested: {_string_or_unknown(interested)}")
        if time_pref not in (None, ""):
            lines.append(f"  - time preference: {time_pref}")
        if slot not in (None, ""):
            lines.append(f"  - selected slot: {slot}")
        if booking_confirmed is not None:
            lines.append(f"  - booking confirmed: {_string_or_unknown(booking_confirmed)}")

    return "\n".join(lines)
