import json
import os
import random
import sys

import chainlit as cl
import yaml
from loguru import logger

from worksheets.agent.agent import Agent
from worksheets.core.dialogue import CurrentDialogueTurn
from worksheets.utils.annotation import get_agent_action_schemas, get_context_schema

current_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(current_dir, "..", ".."))

from ticket_submission.ticket_submission import (
    change_course_service,
    join_waitlist_service,
    prompt_dir,
    submit_api,
    suql_knowledge,
    suql_parser,
)

current_dir = os.path.dirname(os.path.realpath(__file__))
logger.remove()

logger.add(
    os.path.join(current_dir, "..", "user_logs_servicenow", "user_logs.log"),
    rotation="1 day",
)

with open("model_config.yaml", "r") as f:
    model_config = yaml.safe_load(f)


# yelp bot
unhappy_paths = [
    "**- Once you have given some information, change it (e.g, the course name, the issue you were having)**",
    "**- Ask question about how to enroll in a course**",
]

goals = [
    "- You want to applied for leave of absense but cannot check your form status.",
    "- You are having trouble with joining waitlist for a course.",
    "- You cannot find your AP credits in your transcript.",
]

goals = "\n" + "\n".join(goals) + "\n"

unhappy_paths = "\n" + "\n".join(unhappy_paths)


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
    cl.user_session.set(
        "bot",
        Agent(
            botname="TicketSubmissionBot",
            description="You an assistant for Stanford student services. You can help the student with their questions and generate a help ticket if needed.",
            prompt_dir=prompt_dir,
            starting_prompt="""Hello! I'm ServiceBot. I'm here to help you answer you questions and **generate a help ticket**.

I have the following capabilities:
- Troubleshooting Student Enrollment Issues, such as changing course or joining waitlist 
- Issues with Leave of Absence
- Problems with Test submitting scores or missing credits
""",
            args={"model": model_config},
            api=[submit_api, change_course_service, join_waitlist_service],
            knowledge_base=suql_knowledge,
            knowledge_parser=suql_parser,
        ).load_from_gsheet(
            gsheet_id="1aNAG5xh1F_6EmtUAYTmoOBlJBdnpl7lgiZ9YhIW8UxA",
        ),
    )

    user_id = cl.user_session.get("id")
    logger.info(f"Chat started for user {user_id}")
    if not os.path.exists(
        os.path.join(
            current_dir, "..", "benchmarks", "data", "user_conversation", user_id
        )
    ):
        os.mkdir(
            os.path.join(
                current_dir, "..", "benchmarks", "data", "user_conversation", user_id
            )
        )
    await cl.Message(
        f"Here is your user id: **{user_id}**\n"
        + cl.user_session.get("bot").starting_prompt
        + f"\n\nTry to talk for at least 10 turns. Assume that you have one of the following goals or make up one along the similar lines: {goals}"
    ).send()


@cl.on_message
async def get_user_message(message):
    bot = cl.user_session.get("bot")
    await bot.generate_next_turn(message.content)

    cl.user_session.set("bot", bot)

    response = bot.dlg_history[-1].system_response
    await cl.Message(response).send()


@cl.on_chat_end
def on_chat_end():
    user_id = cl.user_session.get("id")
    if not os.path.exists(
        os.path.join(
            current_dir,
            "..",
            "benchmarks",
            "data",
            "servicenow",
            "user_conversation",
            user_id,
        )
    ):
        os.mkdir(
            os.path.join(
                current_dir,
                "..",
                "benchmarks",
                "data",
                "servicenow",
                "user_conversation",
                user_id,
            )
        )

    bot = cl.user_session.get("bot")
    if len(bot.dlg_history):
        file_name = os.path.join(
            current_dir,
            "..",
            "benchmarks",
            "data",
            "servicenow",
            "user_conversation",
            user_id,
            "conversation.json",
        )
        if os.path.exists(file_name):
            file_name = os.path.join(
                current_dir,
                "..",
                "benchmarks",
                "data",
                "servicenow",
                "user_conversation",
                user_id,
                f"conversation_{random.randint(0, 1000)}.json",
            )
        else:
            with open(
                file_name,
                "w",
            ) as f:
                json.dump(convert_to_json(bot.dlg_history), f)
    else:
        os.rmdir(
            os.path.join(
                current_dir,
                "..",
                "benchmarks",
                "data",
                "servicenow",
                "user_conversation",
                user_id,
            )
        )

    logger.info(f"Chat ended for user {user_id}")
