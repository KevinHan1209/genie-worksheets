import datetime
import json
import os
from enum import Enum
from uuid import uuid4

import langchain
from langchain.memory import ChatMessageHistory
from langchain_core.callbacks import FileCallbackHandler, StdOutCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langchain_openai import ChatOpenAI
from loguru import logger
from suql.agent import DialogueTurn

from worksheets.agents.course_enroll.custom_suql import (
    suql_prompt_selector,
    suql_runner,
)
from worksheets.components import CurrentDialogueTurn
from worksheets.llm import llm_generate
from worksheets.llm_utils import extract_code_block_from_output

langchain.debug = True
logfile = "gpt_yelpbot_basic.log"
logger.add(logfile, colorize=True, enqueue=True)
handler_1 = FileCallbackHandler(logfile)
handler_2 = StdOutCallbackHandler()


current_dir = os.path.dirname(os.path.realpath(__file__))

prompt_dir = os.path.join(
    current_dir, "..", "worksheets", "agents", "course_enroll", "prompts"
)

model_name = "gpt-4-turbo"

model = ChatOpenAI(model=model_name)


async def generate_next_turn_async(message, dlg_history, chat_history, db_results):
    @tool
    def submit_course_enrollment(
        student_name: str,
        student_id: str,
        student_email: str,
        course_0_details: dict,
        course_1_details: dict,
        course_2_details: dict | None = None,
    ):
        """
        Submit the course enrollment for the student.

        Args:
            student_name (str): The student's name.
            student_id (str): The student's ID.
            student_email (str): The student's email address.
            course_0_details (dict): The details of the first course.
            course_1_details (dict): The details of the second course.
            course_2_details (dict): The details of the third course. This is optional.
        """
        return {
            "student_name": student_name,
            "student_id": student_id,
            "student_email": student_email,
            "course_0_details": course_0_details,
            "course_1_details": course_1_details,
            "course_2_details": course_2_details,
            "transaction_id": f"{uuid4()}",
        }

    @tool
    def get_student_info(student_name: str, student_id: str, student_email: str):
        """
        Get the student's information from the user.

        Args:
            student_name (str): The student's name.
            student_id (str): The student's ID.
            student_email (str): The student's email address.
        """
        return {
            "student_name": student_name,
            "student_id": student_id,
            "student_email": student_email,
        }

    class GradeType(str, Enum):
        """
        The type of grade for the course.

        CreditNoCredit: Credit/No Credit
        LetterGrade: Letter Grade
        """

        CreditNoCredit = "Credit/No Credit"
        LetterGrade = "Letter Grade"

    @tool
    def get_course_info(course_name: str, grade_type: GradeType, units: int):
        """
        Get the course's information from the user.

        Args:
            course_name (str): The course's name.
            grade_type (str, Enum): The type of grade student wants to take.

                CreditNoCredit: Credit/No Credit
                LetterGrade: Letter Grade
            units (int): The number of units.
        """
        return {
            "course_name": course_name,
            "grade_type": grade_type,
            "units": units,
        }

    def get_all_courses_info(
        course_0_details: dict,
        course_1_details: dict,
        course_2_details: dict | None = None,
    ):
        """
        Get the information of all the courses from the user.

        Args:
            course_0_details (dict): The details of the first course.
            course_1_details (dict): The details of the second course.
            course_2_details (dict): The details of the third course.
        """
        return {
            "course_0_details": course_0_details,
            "course_1_details": course_1_details,
            "course_2_details": course_2_details,
        }

    @tool
    def answer(query: str):
        """
        Use the tool to find any restaurant or answer the user's question.

        Args:
            query (str): User's question."""
        suql_query = suql_sp(query)

        current_dlg_turn.user_target_suql = suql_query
        return suql_runner(suql_query)

    def suql_sp(
        query: str,
    ):
        """
        A SUQL conversational semantic parser, with a pre-set prompt file.
        The function convets the List[CurrentDialogueTurn] to the expected format
        in SUQL (suql.agent.DialogueTurn) and calls the prompt file.

        # Parameters:

        `dlg_history` (List[CurrentDialogueTurn]): a list of past dialog turns.

        `query` (str): the current query to be parsed.

        # Returns:

        `parsed_output` (str): a parsed SUQL output
        """

        suql_dlg_history = []
        for i, turn in enumerate(dlg_history):
            user_target = turn.user_target_suql
            agent_utterance = turn.system_response
            user_utterance = turn.user_utterance

            suql_dlg_history.append(
                DialogueTurn(
                    user_utterance=user_utterance,
                    db_results=db_results[i],
                    user_target=user_target,
                    agent_utterance=agent_utterance,
                )
            )

        prompt_file = suql_prompt_selector(query)

        parsed_output = llm_generate(
            prompt_file,
            prompt_inputs={
                "dlg": suql_dlg_history,
                "query": query,
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "day": datetime.datetime.now().strftime("%A"),
                "day_tmr": (
                    datetime.datetime.now() + datetime.timedelta(days=1)
                ).strftime("%A"),
            },
            prompt_dir=prompt_dir,
            model_name="gpt-3.5-turbo",
            temperature=0.0,
        )

        return extract_code_block_from_output(parsed_output, lang="sql")

    tools = [
        answer,
        get_student_info,
        get_course_info,
        get_all_courses_info,
        submit_course_enrollment,
    ]
    functions = [convert_to_openai_function(t) for t in tools]

    model_with_tools = model.bind_tools(tools)

    chain = model_with_tools

    tool_called = False

    # with_message_history = RunnableWithMessageHistory(chain, get_session_history)
    current_dlg_turn = CurrentDialogueTurn()

    user_input = message

    chat_history.add_message(HumanMessage(content=user_input))
    current_dlg_turn.user_utterance = user_input

    response = chain.invoke(
        chat_history.messages, {"callbacks": [handler_1, handler_2]}
    )

    chat_history.add_message(response)

    tool_response = None
    for tool_call in response.tool_calls:
        selected_tool = {
            "answer": answer,
            "get_student_info": get_student_info,
            "get_course_info": get_course_info,
            "get_all_courses_info": get_all_courses_info,
            "submit_course_enrollment": submit_course_enrollment,
        }[tool_call["name"].lower()]
        try:
            tool_output = selected_tool(tool_call["args"])
        except Exception as e:
            tool_output = {"Error ": +str(e)}
        if tool_call["name"] == "answer":
            db_results.append(tool_output)
            tool_called = True
        chat_history.add_message(
            ToolMessage(json.dumps(tool_output), tool_call_id=tool_call["id"])
        )

        tool_response = chain.invoke(
            chat_history.messages, {"callbacks": [handler_1, handler_2]}
        )

    if not tool_called:
        db_results.append([])

    tool_called = False

    if tool_response:
        chat_history.add_message(AIMessage(content=tool_response.content))
        current_dlg_turn.system_response = tool_response.content
    else:
        current_dlg_turn.system_response = response.content

    dlg_history.append(current_dlg_turn)

    return current_dlg_turn.system_response


def main():
    dlg_history = []
    chat_history = ChatMessageHistory()
    chat_history.add_message(
        SystemMessage(
            content="""You are a course enrollment assistant. You can help students enroll in courses and answer their questions.
            
    Follow these instructions:
    - First ask the student for the details of all the courses they want to enroll in.
    - For each couse you should ask the student for the course name, grade type, and number of units.
    - The student must take at least two courses.
    - Finally, ask the student for their name, ID, and email address.
    - Answer any questions the student has using the `answer tool`
    - Always confirm the information with the student before submitting the course enrollment.
    - After you have all the information, submit the course enrollment using the `submit_course_enrollment` tool and provide the student with the transaction ID.
    """
        )
    )
    db_results = []

    @tool
    def submit_course_enrollment(
        student_name: str,
        student_id: str,
        student_email: str,
        course_0_details: dict,
        course_1_details: dict,
        course_2_details: dict | None = None,
    ):
        """
        Submit the course enrollment for the student.

        Args:
            student_name (str): The student's name.
            student_id (str): The student's ID.
            student_email (str): The student's email address.
            course_0_details (dict): The details of the first course.
            course_1_details (dict): The details of the second course.
            course_2_details (dict): The details of the third course. This is optional.
        """
        return {
            "student_name": student_name,
            "student_id": student_id,
            "student_email": student_email,
            "course_0_details": course_0_details,
            "course_1_details": course_1_details,
            "course_2_details": course_2_details,
            "transaction_id": f"{uuid4()}",
        }

    @tool
    def get_student_info(student_name: str, student_id: str, student_email: str):
        """
        Get the student's information from the user.

        Args:
            student_name (str): The student's name.
            student_id (str): The student's ID.
            student_email (str): The student's email address.
        """
        return {
            "student_name": student_name,
            "student_id": student_id,
            "student_email": student_email,
        }

    class GradeType(str, Enum):
        """
        The type of grade for the course.

        CreditNoCredit: Credit/No Credit
        LetterGrade: Letter Grade
        """

        CreditNoCredit = "Credit/No Credit"
        LetterGrade = "Letter Grade"

    @tool
    def get_course_info(course_name: str, grade_type: GradeType, units: int):
        """
        Get the course's information from the user.

        Args:
            course_name (str): The course's name.
            grade_type (str, Enum): The type of grade student wants to take.

                CreditNoCredit: Credit/No Credit
                LetterGrade: Letter Grade
            units (int): The number of units.
        """
        return {
            "course_name": course_name,
            "grade_type": grade_type,
            "units": units,
        }

    def get_all_courses_info(
        course_0_details: dict,
        course_1_details: dict,
        course_2_details: dict | None = None,
    ):
        """
        Get the information of all the courses from the user.

        Args:
            course_0_details (dict): The details of the first course.
            course_1_details (dict): The details of the second course.
            course_2_details (dict): The details of the third course.
        """
        return {
            "course_0_details": course_0_details,
            "course_1_details": course_1_details,
            "course_2_details": course_2_details,
        }

    @tool
    def answer(query: str):
        """
        Use the tool to find any restaurant or answer the user's question.

        Args:
            query (str): User's question."""
        suql_query = suql_sp(query)

        current_dlg_turn.user_target_suql = suql_query
        return suql_runner(suql_query)

    def suql_sp(
        query: str,
    ):
        """
        A SUQL conversational semantic parser, with a pre-set prompt file.
        The function convets the List[CurrentDialogueTurn] to the expected format
        in SUQL (suql.agent.DialogueTurn) and calls the prompt file.

        # Parameters:

        `dlg_history` (List[CurrentDialogueTurn]): a list of past dialog turns.

        `query` (str): the current query to be parsed.

        # Returns:

        `parsed_output` (str): a parsed SUQL output
        """

        suql_dlg_history = []
        for i, turn in enumerate(dlg_history):
            user_target = turn.user_target_suql
            agent_utterance = turn.system_response
            user_utterance = turn.user_utterance

            suql_dlg_history.append(
                DialogueTurn(
                    user_utterance=user_utterance,
                    db_results=db_results[i],
                    user_target=user_target,
                    agent_utterance=agent_utterance,
                )
            )

        prompt_file = suql_prompt_selector(query)

        parsed_output = llm_generate(
            prompt_file,
            prompt_inputs={
                "dlg": suql_dlg_history,
                "query": query,
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "day": datetime.datetime.now().strftime("%A"),
                "day_tmr": (
                    datetime.datetime.now() + datetime.timedelta(days=1)
                ).strftime("%A"),
            },
            prompt_dir=prompt_dir,
            model_name="gpt-3.5-turbo",
            temperature=0.0,
        )

        return extract_code_block_from_output(parsed_output, lang="sql")

    tools = [
        answer,
        get_student_info,
        get_course_info,
        get_all_courses_info,
        submit_course_enrollment,
    ]
    functions = [convert_to_openai_function(t) for t in tools]

    model_with_tools = model.bind_tools(tools)

    chain = model_with_tools

    tool_called = False

    # with_message_history = RunnableWithMessageHistory(chain, get_session_history)
    current_dlg_turn = CurrentDialogueTurn()

    turn = 0
    max_turns = 20

    while True and turn < max_turns:
        # with_message_history = RunnableWithMessageHistory(chain, get_session_history)
        current_dlg_turn = CurrentDialogueTurn()
        user_input = input("User: ")
        if user_input == "exit":
            break

        chat_history.add_message(HumanMessage(content=user_input))
        current_dlg_turn.user_utterance = user_input

        response = chain.invoke(
            chat_history.messages, {"callbacks": [handler_1, handler_2]}
        )

        chat_history.add_message(response)

        tool_response = None
        for tool_call in response.tool_calls:
            selected_tool = {
                "answer": answer,
                "get_student_info": get_student_info,
                "get_course_info": get_course_info,
                "get_all_courses_info": get_all_courses_info,
                "submit_course_enrollment": submit_course_enrollment,
            }[tool_call["name"].lower()]
            try:
                tool_output = selected_tool(tool_call["args"])
            except Exception as e:
                tool_output = str(e)
            if tool_call["name"] == "answer":
                db_results.append(tool_output)
                tool_called = True
            chat_history.add_message(
                ToolMessage(json.dumps(tool_output), tool_call_id=tool_call["id"])
            )

            tool_response = chain.invoke(
                chat_history.messages, {"callbacks": [handler_1, handler_2]}
            )

        if not tool_called:
            db_results.append([])

        tool_called = False

        if tool_response:
            chat_history.add_message(AIMessage(content=tool_response.content))
            current_dlg_turn.system_response = tool_response.content
        else:
            current_dlg_turn.system_response = response.content

        dlg_history.append(current_dlg_turn)

        print("Bot:", current_dlg_turn.system_response)

        turn += 1


if __name__ == "__main__":
    main()
