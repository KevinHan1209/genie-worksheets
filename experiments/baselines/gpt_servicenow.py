import datetime
import json
import os
from enum import Enum
from typing import List
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

from worksheets.agents.servicebot.custom_suql import suql_runner
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
    current_dir, "..", "worksheets", "agents", "servicebot", "prompts"
)

model_name = "gpt-4-turbo"

model = ChatOpenAI(model=model_name)


async def generate_next_turn_async(message, dlg_history, chat_history, db_results):
    class StudentTaskEnum(str, Enum):
        """
        The type of task student is submitting a ticket for.

        TroubleShootEnrollment: Troubleshoot Enrollment
        LeaveOfAbsence: Leave of Absence
        ExternalTestCredits: External Test Credits
        """

        TroubleShootEnrollment = "Troubleshoot Enrollment"
        LeaveOfAbsence = "Leave of Absence"
        ExternalTestCredits = "External Test Credits"

    @tool
    def submit_ticket(
        student_task: StudentTaskEnum,
        student_name: str,
        additional_details: str,
        troubleshoot_enrollment: dict | None = None,
        leave_of_absence: dict | None = None,
        external_test_credits: dict | None = None,
    ):
        """
        Submit a ticket with the student's task.

        Follow the instructions:
        - You should ask for the troubleshoot enrollment if the student task is 'Troubleshoot Enrollment'.
        - You should ask for the leave of absence if the student task is 'Leave of Absence'.
        - You should ask for the external test credits if the student task is 'External Test Credits'.

        Args:
            student_task (dict): The ticket student is submitting.
            student_name (str): The student's name.
            additional_details (str): Additional details about their ticket
        """

        logger.info(
            f"submit_ticket: {student_task}, {student_name}, {additional_details}, {troubleshoot_enrollment}, {leave_of_absence}, {external_test_credits}"
        )
        return {
            "student_task": student_task,
            "trouble_shoot_issue": troubleshoot_enrollment,
            "leave_of_absence": leave_of_absence,
            "external_test_credits": external_test_credits,
            "student_name": student_name,
            "additional_details": additional_details,
            "transaction_id": str(uuid4()),
        }

    class TroubleShootEnum(str, Enum):
        """
        The type of issue student is facing with their enrollment.

        ChangeCourse: Change the course.
        JoinWaitlist: Join the waitlist.
        """

        ChangeCourse = "Change the course"
        JoinWaitlist = "Join the waitlist"

    @tool
    def troubleshoot_enrollment(
        trouble_shoot_issue: TroubleShootEnum,
        change_course: dict | None = None,
        join_waitlist: dict | None = None,
    ):
        """
        Troubleshoot the enrollment issue.

        Follow these instructions:
        - You should ask for the change course if the trouble shoot issue is 'Change Course'.
        - You should ask for the join waitlist if the trouble shoot issue is 'Join Waitlist'.

        Args:
            trouble_shoot_issue (dict): The issue student is facing with their enrollment.
        """
        return {
            "trouble_shoot_issue": trouble_shoot_issue,
            "change_course": change_course,
            "join_waitlist": join_waitlist,
        }

    class ChangeTypeEnum(str, Enum):
        """
        The type of change student is requesting for their course.

        DropCourse: Drop the course.
        AddCourse: Add the course.
        ChangeUnits: Change the units.
        OtherEnrollmentIssues: Other enrollment issues.
        """

        DropCourse = "Drop the course"
        AddCourse = "Add the course"
        ChangeUnits = "Change the units"
        OtherEnrollmentIssues = "Other enrollment issues"

    @tool
    def change_course(
        change_type: ChangeTypeEnum,
        course_code: str,
        issue_description: str,
    ):
        """
        Change the course.

        Args:
            change_type (dict): The type of change student is requesting for their course.
            course_code (str): The course code.
            issue_description (str): The issue description.
        """
        return {
            "change_type": change_type,
            "course_code": course_code,
            "issue_description": issue_description,
        }

    @tool
    def join_waitlist(
        course_name: str,
        issue_description: str,
        waitlist_confirmation: bool,
        schedule_conflict: bool,
    ):
        """
        Join the waitlist.

        Args:
            course_name (str): The course name.
            issue_description (str): The issue description.
            waitlist_confirmation (bool): Confirmation that the waitlist exists
            schedule_conflict (bool): Whether there is a schedule conflict.
        """

        return {
            "course_name": course_name,
            "issue_description": issue_description,
            "waitlist_confirmation": waitlist_confirmation,
            "schedule_conflict": schedule_conflict,
        }

    class LeaveOfAbsenceEnum(str, Enum):
        """
        The type of issue student is facing with their leave of absence.

        StatusOfForm: Status of the form.
        Others: Other issues.
        """

        StatusOfForm = "Status of the form"
        Others = "Other issues"

    @tool
    def leave_of_absence(
        leave_of_absence_issue: LeaveOfAbsenceEnum,
        issue_description: str,
        form_status: dict | None = None,
    ):
        """
        Leave of Absence.

        Follow these instructions:
        - Ask for the form status if the leave of absence issue is 'StatusOfForm'.

        Args:
            leave_of_absence_issue (str, Enum): The issue student is facing with their leave of absence.
            issue_description (str): The issue description.
            form_status (dict): The status of the form.
        """
        return {
            "leave_of_absence_issue": leave_of_absence_issue,
            "form_status": form_status,
            "issue_description": issue_description,
        }

    class SubmissionMethodEnum(str, Enum):
        """
        The method of submission.

        Email: Email
        InPerson: In person
        """

        Email = "Email"
        InPerson = "In person"

    @tool
    def form_status(submission_method: SubmissionMethodEnum, submission_date: str):
        """
        The status of the form.

        Args:
            submission_method (str, Enum): The method of submission.
            submission_date (str): The submission date.
        """
        return {
            "submission_method": submission_method,
            "submission_date": submission_date,
        }

    class ExternalTestSpecificIssueEnum(str, Enum):
        SubmitInternationalTestForUnitAward = "Submit International Test for Unit Award"
        MissingOrIncorrectTestScore = "Missing or Incorrect Test Score"

    class TestIssuesEnum(str, Enum):
        CreditNotPosted = "Credit Not Posted"
        IncorrectUnits = "Incorrect Units"

    class TestTypeEnum(str, Enum):
        AP = "Advanced Placement (AP)"
        IB = "International Baccalaureate (IB)"

    class InternationalTestTypeEnum(str, Enum):
        CAPE = "Caribbean Advanced Proficiency Examination (CAPE)"
        GCE = "General Certificate of Education (GCE)"
        FB = "French Baccalaureate (FB)"
        GA = "German Abitur (GA)"
        ILC = "Irish Leaving Certificate (ILC)"
        NEWL = "National Examinations in World Languages (NEWL)"

    class SubjectsForUnitAwardEnum(str, Enum):
        Chemisty = "Chemistry"
        ComputerScience = "Computer Science"
        Economics = "Economics"
        Language = "Language"
        Mathematics = "Mathematics"
        Physics = "Physics"

    @tool
    def external_test_credits(
        specific_issue: ExternalTestSpecificIssueEnum,
        test_issues: TestIssuesEnum | None,
        test_type: TestTypeEnum | None,
        time_of_test_score_submission: str | None,
        type_of_international_test: InternationalTestTypeEnum | None,
        subjects_for_unit_award: SubjectsForUnitAwardEnum | None,
    ):
        """
        External Test Credits.

        Args:
            specific_issue (str, Enum): The specific issue student is facing.
            test_issues (str, Enum): The test issues.
            test_type (str, Enum): The test type.
            time_of_test_score_submission (str): The time of test score submission.
            type_of_international_test (str, Enum): The type of international test.
            subjects_for_unit_award (str, Enum): The subjects for unit award.
        """

        return {
            "specific_issue": specific_issue,
            "test_issues": test_issues,
            "test_type": test_type,
            "time_of_test_score_submission": time_of_test_score_submission,
            "type_of_international_test": type_of_international_test,
            "subjects_for_unit_award": subjects_for_unit_award,
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

        prompt_file = "suql_parser.prompt"

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
        submit_ticket,
        troubleshoot_enrollment,
        change_course,
        join_waitlist,
        leave_of_absence,
        form_status,
        external_test_credits,
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
            "submit_ticket": submit_ticket,
            "troubleshoot_enrollment": troubleshoot_enrollment,
            "change_course": change_course,
            "join_waitlist": join_waitlist,
            "leave_of_absence": leave_of_absence,
            "form_status": form_status,
            "external_test_credits": external_test_credits,
        }[tool_call["name"].lower()]
        try:
            tool_output = selected_tool(tool_call["args"])
        except Exception as e:
            tool_output = {"Error ": str(e)}
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
            content="""You an assistant for Stanford student services. You can help the student with their questions and generate a help ticket if needed.
            
    Follow these instructions:
    - Always confirm the information with the student before submitting the ticket.
    - After you have all the information, submit the ticket using the 'submit_ticket' tool.

    For External Test Credits:
    You should ask for:
        - test issues if the specific issue is 'Missing or Incorrect Test Score'.
        - test type if specific issue is 'Missing or Incorrect Test Score' and test issue is Credit Not Posted.
        - time of test score submission if specific issue is 'Missing or Incorrect Test Score' and test issue is Credit Not Posted.
        - type of international test if specifc issue is Submit International Test for Unit Award.
        - subjects for unit award if specifc issue is Submit International Test for Unit Award.
    """
        )
    )
    db_results = []

    class StudentTaskEnum(str, Enum):
        """
        The type of task student is submitting a ticket for.

        TroubleShootEnrollment: Troubleshoot Enrollment
        LeaveOfAbsence: Leave of Absence
        ExternalTestCredits: External Test Credits
        """

        TroubleShootEnrollment = "Troubleshoot Enrollment"
        LeaveOfAbsence = "Leave of Absence"
        ExternalTestCredits = "External Test Credits"

    @tool
    def submit_ticket(
        student_task: StudentTaskEnum,
        student_name: str,
        additional_details: str,
        troubleshoot_enrollment: dict | None = None,
        leave_of_absence: dict | None = None,
        external_test_credits: dict | None = None,
    ):
        """
        Submit a ticket with the student's task.

        Follow the instructions:
        - You should ask for the troubleshoot enrollment if the student task is 'Troubleshoot Enrollment'.
        - You should ask for the leave of absence if the student task is 'Leave of Absence'.
        - You should ask for the external test credits if the student task is 'External Test Credits'.

        Args:
            student_task (dict): The ticket student is submitting.
            student_name (str): The student's name.
            additional_details (str): Additional details about their ticket
        """
        return {
            "student_task": student_task,
            "trouble_shoot_issue": troubleshoot_enrollment,
            "leave_of_absence": leave_of_absence,
            "external_test_credits": external_test_credits,
            "student_name": student_name,
            "additional_details": additional_details,
            "transaction_id": str(uuid4()),
        }

    class TroubleShootEnum(str, Enum):
        """
        The type of issue student is facing with their enrollment.

        ChangeCourse: Change the course.
        JoinWaitlist: Join the waitlist.
        """

        ChangeCourse = "Change the course"
        JoinWaitlist = "Join the waitlist"

    @tool
    def troubleshoot_enrollment(
        trouble_shoot_issue: TroubleShootEnum,
        change_course: dict | None = None,
        join_waitlist: dict | None = None,
    ):
        """
        Troubleshoot the enrollment issue.

        Follow these instructions:
        - You should ask for the change course if the trouble shoot issue is 'Change Course'.
        - You should ask for the join waitlist if the trouble shoot issue is 'Join Waitlist'.

        Args:
            trouble_shoot_issue (dict): The issue student is facing with their enrollment.
        """
        return {
            "trouble_shoot_issue": trouble_shoot_issue,
            "change_course": change_course,
            "join_waitlist": join_waitlist,
        }

    class ChangeTypeEnum(str, Enum):
        """
        The type of change student is requesting for their course.

        DropCourse: Drop the course.
        AddCourse: Add the course.
        ChangeUnits: Change the units.
        OtherEnrollmentIssues: Other enrollment issues.
        """

        DropCourse = "Drop the course"
        AddCourse = "Add the course"
        ChangeUnits = "Change the units"
        OtherEnrollmentIssues = "Other enrollment issues"

    @tool
    def change_course(
        change_type: ChangeTypeEnum,
        course_code: str,
        issue_description: str,
    ):
        """
        Change the course.

        Args:
            change_type (dict): The type of change student is requesting for their course.
            course_code (str): The course code.
            issue_description (str): The issue description.
        """
        return {
            "change_type": change_type,
            "course_code": course_code,
            "issue_description": issue_description,
        }

    @tool
    def join_waitlist(
        course_name: str,
        issue_description: str,
        waitlist_confirmation: bool,
        schedule_conflict: bool,
    ):
        """
        Join the waitlist.

        Args:
            course_name (str): The course name.
            issue_description (str): The issue description.
            waitlist_confirmation (bool): Confirmation that the waitlist exists
            schedule_conflict (bool): Whether there is a schedule conflict.
        """

        return {
            "course_name": course_name,
            "issue_description": issue_description,
            "waitlist_confirmation": waitlist_confirmation,
            "schedule_conflict": schedule_conflict,
        }

    class LeaveOfAbsenceEnum(str, Enum):
        """
        The type of issue student is facing with their leave of absence.

        StatusOfForm: Status of the form.
        Others: Other issues.
        """

        StatusOfForm = "Status of the form"
        Others = "Other issues"

    @tool
    def leave_of_absence(
        leave_of_absence_issue: LeaveOfAbsenceEnum,
        issue_description: str,
        form_status: dict | None = None,
    ):
        """
        Leave of Absence.

        Follow these instructions:
        - Ask for the form status if the leave of absence issue is 'StatusOfForm'.

        Args:
            leave_of_absence_issue (str, Enum): The issue student is facing with their leave of absence.
            issue_description (str): The issue description.
            form_status (dict): The status of the form.
        """
        return {
            "leave_of_absence_issue": leave_of_absence_issue,
            "form_status": form_status,
            "issue_description": issue_description,
        }

    class SubmissionMethodEnum(str, Enum):
        """
        The method of submission.

        Email: Email
        InPerson: In person
        """

        Email = "Email"
        InPerson = "In person"

    @tool
    def form_status(submission_method: SubmissionMethodEnum, submission_date: str):
        """
        The status of the form.

        Args:
            submission_method (str, Enum): The method of submission.
            submission_date (str): The submission date.
        """
        return {
            "submission_method": submission_method,
            "submission_date": submission_date,
        }

    class ExternalTestSpecificIssueEnum(str, Enum):
        SubmitInternationalTestForUnitAward = "Submit International Test for Unit Award"
        MissingOrIncorrectTestScore = "Missing or Incorrect Test Score"

    class TestIssuesEnum(str, Enum):
        CreditNotPosted = "Credit Not Posted"
        IncorrectUnits = "Incorrect Units"

    class TestTypeEnum(str, Enum):
        AP = "Advanced Placement (AP)"
        IB = "International Baccalaureate (IB)"

    class InternationalTestTypeEnum(str, Enum):
        CAPE = "Caribbean Advanced Proficiency Examination (CAPE)"
        GCE = "General Certificate of Education (GCE)"
        FB = "French Baccalaureate (FB)"
        GA = "German Abitur (GA)"
        ILC = "Irish Leaving Certificate (ILC)"
        NEWL = "National Examinations in World Languages (NEWL)"

    class SubjectsForUnitAwardEnum(str, Enum):
        Chemisty = "Chemistry"
        ComputerScience = "Computer Science"
        Economics = "Economics"
        Language = "Language"
        Mathematics = "Mathematics"
        Physics = "Physics"

    @tool
    def external_test_credits(
        specific_issue: ExternalTestSpecificIssueEnum,
        test_issues: TestIssuesEnum | None,
        test_type: TestTypeEnum | None,
        time_of_test_score_submission: str | None,
        type_of_international_test: InternationalTestTypeEnum | None,
        subjects_for_unit_award: SubjectsForUnitAwardEnum | None,
    ):
        """
        External Test Credits.

        Args:
            specific_issue (str, Enum): The specific issue student is facing with their external test credits.
            test_issues (str, Enum): The test issues.
            test_type (str, Enum): The test type.
            time_of_test_score_submission (str): The time of test score submission.
            type_of_international_test (str, Enum): The type of international test.
            subjects_for_unit_award (str, Enum): The subjects for unit award.
        """

        return {
            "specific_issue": specific_issue,
            "test_issues": test_issues,
            "test_type": test_type,
            "time_of_test_score_submission": time_of_test_score_submission,
            "type_of_international_test": type_of_international_test,
            "subjects_for_unit_award": subjects_for_unit_award,
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

        prompt_file = "suql_parser.prompt"

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
        submit_ticket,
        troubleshoot_enrollment,
        change_course,
        join_waitlist,
        leave_of_absence,
        form_status,
        external_test_credits,
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

        current_dlg_turn.user_utterance = user_input

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
                "submit_ticket": submit_ticket,
                "troubleshoot_enrollment": troubleshoot_enrollment,
                "change_course": change_course,
                "join_waitlist": join_waitlist,
                "leave_of_absence": leave_of_absence,
                "form_status": form_status,
                "external_test_credits": external_test_credits,
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
