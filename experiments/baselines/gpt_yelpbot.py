import datetime
import json
import os
from uuid import uuid4

import langchain
import openai
from langchain.memory import ChatMessageHistory
from langchain_core.callbacks import FileCallbackHandler, StdOutCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langchain_openai import ChatOpenAI
from loguru import logger
from suql.agent import DialogueTurn

from worksheets.agents.yelpbot.custom_suql import suql_runner
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
    current_dir, "..", "worksheets", "agents", "yelpbot", "prompts"
)

model_name = "gpt-4-turbo"

model = ChatOpenAI(model=model_name)

# config = {"configurable": {"session_id": unique_id}}
max_turns = 20
turn = 0


async def generate_next_turn_async(message, dlg_history, chat_history, db_results):
    @tool
    def book_restaurant(
        restaurant_id: str,
        date: str,
        time: str,
        num_people: int,
        special_requests: str | None = None,
    ):
        """
        Book a restaurant for the user.

        Args:
            restaurant (str): The name of the restaurant the user wants to book.
            date (str): The date the user wants to book the restaurant.
            time (str): The time the user wants to book the restaurant.
            num_people (int): The number of people in the who will be dining.
            special_requests (str | None): Any special requests the user has for the restaurant.
        """
        return {
            "status": "success",
            "message": f"Booked {num_people} people at {restaurant_id} on {date} at {time}.",
            "transaction_id": str(uuid4()),
        }

    @tool
    def answer(query: str):
        """
        Use the tool to find any restaurant or answer the user's question.

        Args:
            query (str): User's question."""
        suql_query = suql_sp(query)

        current_dlg_turn.user_target_suql = suql_query
        return suql_runner(
            suql_query,
            [
                "id",
                "name",
                "cuisines",
                "price",
                "rating",
                "num_reviews",
                "address",
                "phone_number",
                "popular_dishes",
                "location",
                "opening_hours",
                "summary",
                "answer",
            ],
        )

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

    tools = [book_restaurant, answer]
    functions = [convert_to_openai_function(t) for t in tools]

    model_with_tools = model.bind_tools(tools)

    chain = model_with_tools

    tool_called = False

    # with_message_history = RunnableWithMessageHistory(chain, get_session_history)
    current_dlg_turn = CurrentDialogueTurn()
    print(message)
    user_input = message

    chat_history.add_message(HumanMessage(content=user_input))
    current_dlg_turn.user_utterance = user_input

    response = chain.invoke(
        chat_history.messages, {"callbacks": [handler_1, handler_2]}
    )

    chat_history.add_message(response)
    tool_response = None
    for tool_call in response.tool_calls:
        selected_tool = {"book_restaurant": book_restaurant, "answer": answer}[
            tool_call["name"].lower()
        ]
        try:
            tool_output = selected_tool(tool_call["args"])
        except Exception as e:
            tool_output = {"error": str(e)}
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


if __name__ == "__main__":
    chat_history = ChatMessageHistory()
    chat_history.add_message(
        SystemMessage(
            content="""You are the restaurant booking assistant!
        
Follow these instructions:
- Use the answer function to answer to user's questions.
- Use the book_restaurant function to book a restaurant.
- If the user asks for a restuarant and they are do not want to book, then propose to book the restaurant.
- Confirm the booking details before booking the restaurant.
- Give the user the transaction id after booking the restaurant.
- If the user asks for restaurants then ask for location first.
- Always ask for the arguments one by one.
"""
        )
    )

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

    @tool
    def book_restaurant(
        restaurant_id: str,
        date: str,
        time: str,
        num_people: int,
        special_requests: str | None = None,
    ):
        """
        Book a restaurant for the user.

        Args:
            restaurant (str): The name of the restaurant the user wants to book.
            date (str): The date the user wants to book the restaurant.
            time (str): The time the user wants to book the restaurant.
            num_people (int): The number of people in the who will be dining.
            special_requests (str | None): Any special requests the user has for the restaurant.
        """
        return {
            "status": "success",
            "message": f"Booked {num_people} people at {restaurant_id} on {date} at {time}.",
            "transaction_id": str(uuid4()),
        }

    @tool
    def answer(query: str):
        """
        Use the tool to find any restaurant or answer the user's question.

        Args:
            query (str): User's question."""
        suql_query = suql_sp(query)

        current_dlg_turn.user_target_suql = suql_query
        return suql_runner(
            suql_query,
            [
                "id",
                "name",
                "cuisines",
                "price",
                "rating",
                "num_reviews",
                "address",
                "phone_number",
                "popular_dishes",
                "location",
                "opening_hours",
                "summary",
                "answer",
            ],
        )

    tools = [book_restaurant, answer]
    functions = [convert_to_openai_function(t) for t in tools]

    model_with_tools = model.bind_tools(tools)

    chain = model_with_tools

    db_results = []

    dlg_history = []
    tool_called = False

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
            selected_tool = {"book_restaurant": book_restaurant, "answer": answer}[
                tool_call["name"].lower()
            ]
            try:
                tool_output = selected_tool(tool_call["args"])
            except openai.BadRequestError as e:
                tool_output = {"error": str(e)}
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

        print("Assistant:", current_dlg_turn.system_response)
        turn += 1
