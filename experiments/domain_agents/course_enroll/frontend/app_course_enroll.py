import json
import os
import random
import sys
from typing import Any, Dict, List
from uuid import uuid4

import chainlit as cl
from loguru import logger

from worksheets import Config
from worksheets.agent.chainlit import ChainlitAgent
from worksheets.core.dialogue import CurrentDialogueTurn
from worksheets.core.worksheet import get_genie_fields_from_ws
from worksheets.utils.annotation import get_agent_action_schemas, get_context_schema

# Add local domain_agents root so `course_enroll.*` imports work on any machine.
CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(CURRENT_DIR, "..", ".."))

from course_enroll.course_enroll import agent_builder

# Define your APIs
course_is_full = {}


def course_detail_to_individual_params(course_detail):
    if course_detail.value is None:
        return {}
    course_detail = course_detail.value
    course_detail = {}
    for field in get_genie_fields_from_ws(course_detail):
        course_detail[field.name] = field.value

    return course_detail


def courses_to_take_oval(**kwargs):
    return {"success": True, "transaction_id": uuid4()}


def is_course_full(course_id, **kwargs):
    # randomly return True or False
    if course_id not in course_is_full:
        is_full = random.choice([True, False])
        course_is_full[course_id] = is_full

    return course_is_full[course_id]


# Constants
DATA_DIR = os.path.join(CURRENT_DIR, "data", "user_conversation")
LOGS_DIR = os.path.join(CURRENT_DIR, "..", "user_logs_courseenroll")
LOGS_FILE = os.path.join(LOGS_DIR, "user_logs_230325.log")


# Configure logger
logger.remove()
logger.add(LOGS_FILE, rotation="1 day")

# Load configurations from YAML

config = Config.load_from_yaml(os.path.join(CURRENT_DIR, "..", "config.yaml"))

def convert_to_json(dialogue: List[CurrentDialogueTurn]) -> List[Dict[str, Any]]:
    """
    Convert dialogue history to JSON format for storage.

    Args:
        dialogue: List of dialogue turns

    Returns:
        List of dialogue turns in JSON format
    """
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


def get_user_conversation_dir(user_id: str) -> str:
    """
    Get the user conversation directory path.

    Args:
        user_id: User ID

    Returns:
        Path to user conversation directory
    """
    return os.path.join(DATA_DIR, user_id)


def ensure_user_dir_exists(user_id: str) -> None:
    """
    Ensure that the user conversation directory exists.

    Args:
        user_id: User ID
    """
    user_dir = get_user_conversation_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)


def save_conversation(
    user_id: str, dialogue: List[CurrentDialogueTurn], filename: str
) -> str:
    """
    Save conversation to a file.

    Args:
        user_id: User ID
        dialogue: Dialogue history
        filename: Filename to save to

    Returns:
        Path to saved file
    """
    user_dir = get_user_conversation_dir(user_id)
    file_path = os.path.join(user_dir, filename)

    with open(file_path, "w") as f:
        json.dump(convert_to_json(dialogue), f, indent=4)

    return file_path


def create_agent(model: str = "GPT-4o") -> ChainlitAgent:
    """
    Create and configure the course enrollment agent.

    Returns:
        Configured ChainlitAgent
    """
    return agent_builder.build(config, ChainlitAgent)


@cl.set_chat_profiles
async def chat_profile():
    return [
        cl.ChatProfile(
            name="GPT-4o",
            markdown_description="Course Enrollment Assistant (GPT-4o)",
            icon="https://www.wwknowledge.org/assets/logos/oval-logo.png",
        ),
        cl.ChatProfile(
            name="GPT-4o-mini",
            markdown_description="Course Enrollment Assistant (GPT-4o-mini)",
            icon="https://www.wwknowledge.org/assets/logos/oval-logo.png",
        ),
    ]


@cl.on_chat_start
async def initialize():
    """Initialize the chat session when a user starts a conversation."""
    chat_profile = cl.user_session.get("chat_profile")
    agent = create_agent(model=chat_profile)
    cl.user_session.set("bot", agent)

    user_id = cl.user_session.get("id")
    logger.info(f"Chat started for user {user_id}")

    ensure_user_dir_exists(user_id)

    welcome_message = (
        f"Here is your user id: **{user_id}**\n"
        f"{agent.starting_prompt}\n\n"
        # "Please be a user who asks several questions.\n\n"
        # "It is mandatory for users (you) to enroll in at least two courses.\n"
        "**Note:** The database only contains Computer Science courses and contains course data till the academic 2023."
    )

    await cl.Message(welcome_message).send()


@cl.on_message
async def get_user_message(message):
    """Handle user messages and generate responses."""
    agent = cl.user_session.get("bot")
    await agent.generate_next_turn(message.content)

    cl.user_session.set("bot", agent)
    user_id = cl.user_session.get("id")
    response = agent.dlg_history[-1].system_response
    # Guard against null/empty responses that can break frontend rendering.
    if response is None:
        response = "I couldn't generate a response. Please try again."
    else:
        response = str(response)

    ensure_user_dir_exists(user_id)

    # Save intermediate conversation
    file_path = save_conversation(
        user_id, agent.dlg_history, "intermediate_conversation.json"
    )

    # Send response with conversation log attachment when possible.
    elements = []
    if file_path and os.path.exists(file_path):
        elements = [cl.File(name="conv_log.json", path=file_path)]

    try:
        await cl.Message(
            response,
            author="Course Enrollment Assistant",
            elements=elements,
        ).send()
    except Exception:
        # Fallback path: send plain text if attachment rendering fails in UI.
        await cl.Message(
            response,
            author="Course Enrollment Assistant",
        ).send()


@cl.on_chat_end
def on_chat_end():
    """Handle the end of a chat session."""
    user_id = cl.user_session.get("id")
    agent = cl.user_session.get("bot")

    ensure_user_dir_exists(user_id)

    if agent.dlg_history:
        user_dir = get_user_conversation_dir(user_id)
        file_name = "conversation.json"
        file_path = os.path.join(user_dir, file_name)

        # If file already exists, create a new one with random suffix
        if os.path.exists(file_path):
            file_name = f"conversation_{random.randint(0, 1000)}.json"
            file_path = os.path.join(user_dir, file_name)

        save_conversation(user_id, agent.dlg_history, file_name)
    else:
        # If no dialogue history, remove the user directory
        try:
            user_dir = get_user_conversation_dir(user_id)
            os.rmdir(user_dir)
        except OSError:
            # Directory might not be empty or might not exist
            logger.warning(f"Could not remove directory for user {user_id}")

    logger.info(f"Chat ended for user {user_id}")
