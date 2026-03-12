import os
from typing import Type

from worksheets import Config, SUQLKnowledgeBase
from worksheets.agent.agent import Agent
from worksheets.core.runtime import GenieRuntime
from worksheets.knowledge.parser import SUQLReActParser

from insurance_lead_followup.api import (
    build_confirmation_summary,
    finalize_confirmed_main,
    _get_db_connection,
    book_appointment,
    check_availability,
    hydrate_verify_quote,
    process_lead_outcome,
    restore_control_db_state,
    snapshot_control_db_state,
    sync_lead_contact,
    sync_schedule_consultation,
    sync_underwriting_intake,
    sync_verify_quote,
)
from insurance_lead_followup.worksheets import (
    LeadContact,
    Main,
    ScheduleConsultation,
    UnderwritingIntake,
    VerifyQuote,
)

current_dir = os.path.dirname(os.path.realpath(__file__))

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


def build_insurance_lead_worksheet_agent(
    config: Config, agent_class: Type[Agent] = Agent
):
    cfg = _get_db_config()
    knowledge_base = SUQLKnowledgeBase(
        config.knowledge_base,
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
            "insurance_general_info": os.path.join(current_dir, "insurance_general_info.txt")
        },
        db_username=cfg["user"],
        db_password=cfg["password"],
        db_host=cfg["host"],
        db_port=cfg["port"],
    )
    knowledge_parser = SUQLReActParser(
        config.knowledge_parser,
        knowledge=knowledge_base,
        example_path=os.path.join(current_dir, "examples.txt"),
        instruction_path=os.path.join(current_dir, "instructions.txt"),
        table_schema_path=os.path.join(current_dir, "table_schema.txt"),
    )

    apis = [
        process_lead_outcome,
        check_availability,
        book_appointment,
        hydrate_verify_quote,
        build_confirmation_summary,
        sync_lead_contact,
        sync_verify_quote,
        sync_underwriting_intake,
        sync_schedule_consultation,
        finalize_confirmed_main,
        snapshot_control_db_state,
        restore_control_db_state,
    ]
    agent = agent_class(
        botname="InsuranceLeadBot",
        description=DESCRIPTION,
        starting_prompt=_build_starting_prompt(lead_id=1),
        config=config,
        api=apis,
        knowledge_base=knowledge_base,
        knowledge_parser=knowledge_parser,
    )

    runtime = GenieRuntime(
        config=config,
        api=apis,
        suql_runner=knowledge_base.run,
        agent=agent,
    )
    # Main first to keep worksheet creation rooted in the orchestrator.
    for ws in [Main, LeadContact, VerifyQuote, UnderwritingIntake, ScheduleConsultation]:
        runtime.add_worksheet(ws)

    agent.runtime = runtime
    agent._initialize_modules()
    return agent
