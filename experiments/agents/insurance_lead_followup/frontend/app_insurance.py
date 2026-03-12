import json
import os
import sys

import chainlit as cl
from loguru import logger

from worksheets import Config
from worksheets.agent.chainlit import ChainlitAgent
from worksheets.agent.config import OpenAIModelConfig
from worksheets.core.dialogue import CurrentDialogueTurn
from worksheets.utils.annotation import get_agent_action_schemas, get_context_schema

current_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(current_dir, "..", ".."))

from insurance_lead_followup.worksheet_agent_builder import (  # noqa: E402
    STARTING_PROMPT,
    build_insurance_lead_worksheet_agent,
    reset_db_to_default,
)
from insurance_lead_followup.api import (  # noqa: E402
    create_chat_schema_from_control,
    drop_schema,
    ensure_control_schema_seeded,
    export_schema_state,
    promote_chat_schema_to_control,
    set_active_db_schema,
)

logger.remove()
user_logs_dir = os.path.join(current_dir, "..", "user_logs")
os.makedirs(user_logs_dir, exist_ok=True)
logger.add(
    os.path.join(user_logs_dir, "user_logs.log"),
    rotation="1 day",
)


def convert_to_json(dialogue: list[CurrentDialogueTurn]):
    json_dialogue = []
    for turn in dialogue:
        json_turn = {
            "user": turn.user_utterance,
            "bot": turn.system_response,
            "turn_context": get_context_schema(turn.context),
            "global_context": get_context_schema(turn.global_context),
            "system_action": get_agent_action_schemas(turn.system_action),
            "user_target_sp": turn.user_target_sp,
            "user_target": turn.user_target,
            "user_target_suql": turn.user_target_suql,
        }
        json_dialogue.append(json_turn)
    return json_dialogue


@cl.on_chat_start
async def initialize():
    user_id = cl.user_session.get("id")
    user_conv_dir = os.path.join(current_dir, "user_conversation")
    os.makedirs(user_conv_dir, exist_ok=True)
    user_dir = os.path.join(user_conv_dir, user_id)
    os.makedirs(user_dir, exist_ok=True)

    should_reset = os.getenv("INSURANCE_RESET_DB_ON_CHAT", "").lower() in (
        "1",
        "true",
        "yes",
    )

    if should_reset:
        try:
            reset_db_to_default()
            logger.info("DB reset to default at chat start")
        except Exception as e:
            logger.warning(f"DB reset failed at chat start: {e}")

    control_result = ensure_control_schema_seeded()
    logger.info(f"Control schema init result at chat start: {control_result}")

    chat_schema_result = create_chat_schema_from_control(chat_id=user_id)
    chat_schema = chat_schema_result.get("chat_schema")
    cl.user_session.set("chat_schema", chat_schema)
    logger.info(f"Chat schema init result at chat start: {chat_schema_result}")
    set_active_db_schema(chat_schema)

    config = Config(
        semantic_parser=OpenAIModelConfig(model_name="gpt-4o"),
        response_generator=OpenAIModelConfig(model_name="gpt-4o"),
        knowledge_parser=OpenAIModelConfig(model_name="gpt-4o"),
        knowledge_base=OpenAIModelConfig(model_name="gpt-4o"),
    )
    cl.user_session.set(
        "bot",
        build_insurance_lead_worksheet_agent(config, agent_class=ChainlitAgent),
    )
    logger.info(f"Chat started for user {user_id}")

    await cl.Message(
        f"Here is your user id: **{user_id}**\n{STARTING_PROMPT}"
    ).send()


@cl.on_message
async def get_user_message(message):
    bot = cl.user_session.get("bot")
    await bot.generate_next_turn(message.content)
    cl.user_session.set("bot", bot)
    await cl.Message(bot.dlg_history[-1].system_response).send()


@cl.on_chat_end
def on_chat_end(*args, **kwargs):
    user_id = cl.user_session.get("id")
    chat_schema = cl.user_session.get("chat_schema")
    user_conv_dir = os.path.join(current_dir, "user_conversation")
    user_dir = os.path.join(user_conv_dir, user_id)
    os.makedirs(user_dir, exist_ok=True)

    bot = cl.user_session.get("bot")
    if len(bot.dlg_history):
        with open(
            os.path.join(user_dir, "conversation.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(convert_to_json(bot.dlg_history), f)
    else:
        try:
            os.rmdir(user_dir)
        except OSError:
            logger.warning(f"Could not remove non-empty directory: {user_dir}")

    if chat_schema:
        set_active_db_schema(chat_schema)
        try:
            export_result = export_schema_state(
                schema_name=chat_schema,
                output_path=os.path.join(user_dir, "db_state.json"),
                lead_id=1,
            )
            logger.info(
                f"Chat DB state export result after chat ended for user {user_id}: "
                f"{export_result}"
            )
        except Exception as e:
            logger.warning(f"Chat DB state export failed for user {user_id}: {e}")

        should_promote = os.getenv(
            "INSURANCE_PROMOTE_CHAT_TO_CONTROL_ON_END", ""
        ).lower() in ("1", "true", "yes")
        if should_promote:
            try:
                promote_result = promote_chat_schema_to_control(chat_schema=chat_schema)
                logger.info(
                    f"Control promotion result after chat ended for user {user_id}: "
                    f"{promote_result}"
                )
            except Exception as e:
                logger.warning(f"Control promotion failed for user {user_id}: {e}")

        should_drop_chat_schema = os.getenv(
            "INSURANCE_DROP_CHAT_SCHEMA_ON_END", ""
        ).lower() in ("1", "true", "yes")
        if should_drop_chat_schema:
            try:
                drop_result = drop_schema(chat_schema)
                logger.info(
                    f"Chat schema drop result after chat ended for user {user_id}: "
                    f"{drop_result}"
                )
            except Exception as e:
                logger.warning(f"Chat schema drop failed for user {user_id}: {e}")

    should_reset = os.getenv("INSURANCE_RESET_DB_ON_CHAT", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if should_reset:
        try:
            reset_db_to_default()
            logger.info(f"DB reset to default after chat ended for user {user_id}")
        except Exception as e:
            logger.warning(f"DB reset failed for user {user_id}: {e}")

    set_active_db_schema(None)
    logger.info(f"Chat ended for user {user_id}")
