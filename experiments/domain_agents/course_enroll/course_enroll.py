import asyncio
import os
import random
from uuid import uuid4

from suql.agent import postprocess_suql

from worksheets import (
    AgentBuilder,
    Config,
    SUQLKnowledgeBase,
    SUQLReActParser,
    conversation_loop,
)
from worksheets.agent.builder import TemplateLoader
from worksheets.agent.config import agent_api
from worksheets.core.worksheet import get_genie_fields_from_ws

# Define your APIs
course_is_full = {}


@agent_api("course_detail_to_individual_params", "Get course details")
def course_detail_to_individual_params(course_detail):
    if course_detail.value is None:
        return {}
    course_detail = course_detail.value
    course_details = {}
    for field in get_genie_fields_from_ws(course_detail):
        course_details[field.name] = field.value

    return course_details


@agent_api("courses_to_take_oval", "Enroll into a course")
def courses_to_take_oval(**kwargs):
    return {"success": True, "transaction_id": uuid4()}


@agent_api("is_course_full", "Check if a course is full")
def is_course_full(course_id, **kwargs):
    # randomly return True or False
    if course_id not in course_is_full:
        is_full = random.choice([True, False])
        course_is_full[course_id] = is_full

    return course_is_full[course_id]


# Define path to the prompts

current_dir = os.path.dirname(os.path.realpath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")


config = Config.load_from_yaml(os.path.join(current_dir, "config.yaml"))

starting_prompt = TemplateLoader.load(
    os.path.join(current_dir, "starting_prompt.md"), format="jinja2"
)

agent_builder = (
    AgentBuilder(
        name="Course Enrollment Assistant",
        description="You are a course enrollment assistant. You can help students with course selection and enrollment.",
        starting_prompt=starting_prompt.render(),
    )
    .with_knowledge_base(
        SUQLKnowledgeBase,
        tables_with_primary_keys={
            "courses": "course_id",
            "ratings": "rating_id",
            "offerings": "course_id",
            "programs": "program_id",
        },
        database_name="course_assistant",
        embedding_server_address="http://127.0.0.1:8509",
        source_file_mapping={
            "course_assistant_general_info.txt": os.path.join(
                current_dir, "course_assistant_general_info.txt"
            )
        },
        postprocessing_fn=postprocess_suql,
        result_postprocessing_fn=None,
        db_username="select_user",
        db_password="select_user",
    )
    .with_parser(
        SUQLReActParser,
        example_path=os.path.join(current_dir, "examples.txt"),
        instruction_path=os.path.join(current_dir, "instructions.txt"),
        table_schema_path=os.path.join(current_dir, "table_schema.txt"),
    )
    # Use local specification so samples run without Google service-account setup.
    .with_json_specification(os.path.join(current_dir, "course_enrollment.json"))
)

agent = agent_builder.build(config)

if __name__ == "__main__":
    # Run the conversation loop in the terminal
    asyncio.run(conversation_loop(agent, "course_assistant_bot_new.json", debug=True))
