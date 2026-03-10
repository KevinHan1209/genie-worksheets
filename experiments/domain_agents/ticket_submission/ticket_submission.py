import asyncio
import os
from uuid import uuid4

from suql.agent import postprocess_suql

from worksheets.agent.agent import Agent
from worksheets.knowledge.base import SUQLKnowledgeBase
from worksheets.knowledge.parser import SUQLParser
from worksheets.utils.interface import conversation_loop


# Define your APIs
def submit_api(
    student_task,
    trouble_shoot_student_enrollment,
    leave_of_absence,
    external_test_credits,
    full_name,
    **kwargs,
):
    task = None
    if trouble_shoot_student_enrollment.value:
        task = trouble_shoot_student_enrollment.value
    elif leave_of_absence.value:
        task = leave_of_absence.value
    elif external_test_credits.value:
        task = external_test_credits.value

    return {
        "status": "success",
        "params": {
            "student_task": student_task.value,
            "task": task,
            "full_name": full_name.value,
        },
        "response": {
            "transaction_id": uuid4(),
        },
    }


def change_course_service(
    change_type: str,
    course_id: str,
    class_number: int,
    issue_description: str,
):
    outcome = {
        "status": "success",
        "params": {
            "change_type": change_type.value,
            "course_id": course_id.value,
            "class_number": class_number.value,
            "issue_description": issue_description.value,
        },
        "response": {
            "transaction_id": uuid4(),
        },
    }
    return outcome


def join_waitlist_service(
    course_name: str,
    class_number: int,
    issue_description: str,
    waitlist_confirmation: str,
    schedule_conflict,
):
    return {
        "status": "success",
        "params": {
            "course_name": course_name.value,
            "class_number": class_number.value,
            "issue_description": issue_description.value,
            "waitlist_confirmation": waitlist_confirmation.value,
        },
        "response": {
            "transaction_id": uuid4(),
        },
    }


# Define path to the prompts

current_dir = os.path.dirname(os.path.realpath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")

# Define Knowledge Base
suql_knowledge = SUQLKnowledgeBase(
    llm_model_name="gpt-4o",
    tables_with_primary_keys={},
    database_name="services_assistant",
    embedding_server_address="http://127.0.0.1:8509",
    source_file_mapping={
        "services_general_info": os.path.join(current_dir, "services_general_info.txt")
    },
    postprocessing_fn=postprocess_suql,
    result_postprocessing_fn=None,
)

# Define the SUQL Parser
suql_parser = SUQLParser(
    llm_model_name="gpt-4o",
    knowledge=suql_knowledge,
)

# Define the agent
ticket_submission_bot = Agent(
    botname="TicketSubmissionBot",
    description="You an assistant for Stanford student services. You can help the student with their questions and generate a help ticket if needed.",
    prompt_dir=prompt_dir,
    starting_prompt="""Hello! I'm ServiceBot. I'm here to help you answer you questions and **generate a help ticket**.

I have the following capabilities:
- Troubleshooting Student Enrollment Issues, such as changing course or joining waitlist 
- Issues with Leave of Absence
- Problems with Test submitting scores or missing credits
""",
    args={},
    api=[submit_api, change_course_service, join_waitlist_service],
    knowledge_base=suql_knowledge,
    knowledge_parser=suql_parser,
).load_from_gsheet(
    gsheet_id="1aNAG5xh1F_6EmtUAYTmoOBlJBdnpl7lgiZ9YhIW8UxA",
)

if __name__ == "__main__":
    # Run the conversation loop
    asyncio.run(conversation_loop(ticket_submission_bot, "ticket_submission_bot.json"))
