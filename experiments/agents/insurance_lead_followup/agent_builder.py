import os
from typing import Type

from worksheets import AgentBuilder, Config, SUQLKnowledgeBase
from worksheets.agent.agent import Agent
from worksheets.knowledge.parser import SUQLReActParser

from insurance_lead_followup.api import (
    _get_db_connection,
    book_appointment,
    check_availability,
    finalize_confirmed_main,
    hydrate_verify_quote,
    process_lead_outcome,
    restore_control_db_state,
    snapshot_control_db_state,
    sync_lead_contact,
    sync_schedule_consultation,
    sync_underwriting_intake,
    sync_verify_quote,
)

current_dir = os.path.dirname(os.path.realpath(__file__))

SPEC_PATH = os.path.join(current_dir, "insurance_lead_spec.json")

DESCRIPTION = (
    "You are an outbound follow-up assistant for Prestige Home Insurance. "
    "You are not a licensed insurance agent. Confirm identity, verify quote "
    "details, collect underwriting information, and schedule consultations "
    "with a licensed agent."
)

STARTING_PROMPT = (
    "Hello, this is an automated assistant calling on behalf of Prestige Home "
    "Insurance. Am I speaking with Kevin?"
)


def _get_target_name_for_prompt(lead_id: int = 1) -> str:
    fallback_name = os.getenv("INSURANCE_DEFAULT_TARGET_NAME", "Kevin")
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT target_name FROM insurance_leads WHERE lead_id = %s;",
                    (lead_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
        finally:
            conn.close()
    except Exception:
        pass
    return fallback_name


def _build_starting_prompt(lead_id: int = 1) -> str:
    target_name = _get_target_name_for_prompt(lead_id=lead_id)
    return (
        "Hello, this is an automated assistant calling on behalf of Prestige Home "
        f"Insurance. Am I speaking with {target_name}?"
    )


def _get_db_config():
    return {
        "host": os.getenv("INSURANCE_DB_HOST", "127.0.0.1"),
        "port": os.getenv("INSURANCE_DB_PORT", "5432"),
        "user": os.getenv("INSURANCE_DB_USER", "select_user"),
        "password": os.getenv("INSURANCE_DB_PASSWORD", "select_user"),
        "database": os.getenv("INSURANCE_DB_NAME", "insurance"),
    }


def reset_db_to_default():
    from insurance_lead_followup.local_setup.seed_insurance_db import (
        DbConfig,
        connect,
        ensure_schema,
        seed_data,
    )

    cfg_dict = _get_db_config()
    cfg = DbConfig(
        host=cfg_dict["host"],
        port=int(cfg_dict["port"]),
        user=cfg_dict["user"],
        password=cfg_dict["password"],
        database=cfg_dict["database"],
    )
    conn = connect(cfg)
    try:
        ensure_schema(conn)
        seed_data(conn, reset=True)
        conn.commit()
    finally:
        conn.close()


def build_insurance_lead_agent(config: Config, agent_class: Type[Agent] = Agent):
    cfg = _get_db_config()
    return (
        AgentBuilder(
            name="InsuranceLeadBot",
            description=DESCRIPTION,
            starting_prompt=_build_starting_prompt(lead_id=1),
        )
        .add_api(process_lead_outcome)
        .add_api(check_availability)
        .add_api(book_appointment)
        .add_api(hydrate_verify_quote)
        .add_api(sync_lead_contact)
        .add_api(sync_verify_quote)
        .add_api(sync_underwriting_intake)
        .add_api(sync_schedule_consultation)
        .add_api(finalize_confirmed_main)
        .add_api(snapshot_control_db_state)
        .add_api(restore_control_db_state)
        .with_knowledge_base(
            SUQLKnowledgeBase,
            tables_with_primary_keys={
                "insurance_leads": "lead_id",
                "insurance_quotes": "quote_id",
                "insurance_underwriting": "underwriting_id",
                "insurance_appointments": "appointment_id",
                "insurance_call_log": "call_id",
            },
            database_name=cfg["database"],
            embedding_server_address="http://127.0.0.1:8509",
            source_file_mapping={
                "insurance_general_info": os.path.join(
                    current_dir, "insurance_general_info.txt"
                )
            },
            db_username=cfg["user"],
            db_password=cfg["password"],
            db_host=cfg["host"],
            db_port=cfg["port"],
        )
        .with_parser(
            SUQLReActParser,
            example_path=os.path.join(current_dir, "examples.txt"),
            instruction_path=os.path.join(current_dir, "instructions.txt"),
            table_schema_path=os.path.join(current_dir, "table_schema.txt"),
        )
        .with_json_specification(SPEC_PATH)
        .build(config, agent_class=agent_class)
    )
