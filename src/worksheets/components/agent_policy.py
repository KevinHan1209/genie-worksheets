import inspect
from dataclasses import dataclass
from typing import Any, Dict, List, Type, Union

from loguru import logger

from worksheets.config.settings import OPEN_NEW_WORKSHEET_IF_POSSIBLE
from worksheets.core import (
    CurrentDialogueTurn,
    GenieContext,
    GenieField,
    GenieRuntime,
    GenieValue,
)
from worksheets.core.agent_acts import (
    AgentAct,
    AskAgentAct,
    AskForConfirmationAgentAct,
    ReportAgentAct,
)
from worksheets.core.worksheet import Answer, GenieType, GenieWorksheet
from worksheets.utils.field import get_genie_fields_from_ws
from worksheets.utils.logging_config import (
    log_action_result,
    log_context,
    log_worksheet_state,
)
from worksheets.utils.predicates import eval_predicates
from worksheets.utils.variable import generate_var_name, get_variable_name
from worksheets.utils.worksheet import (
    any_open_empty_ws,
    count_worksheet_variables,
    genie_deepcopy,
    same_field,
    same_worksheet,
)


@dataclass
class ObjectCollection:
    """Collection of different types of objects found in the context.

    Attributes:
        answer_objects (List[Answer]): List of Answer objects.
        worksheet_objects (List[GenieWorksheet]): List of Worksheet objects.
        type_objects (List[GenieType]): List of Type objects.
    """

    answer_objects: List[Answer]
    worksheet_objects: List[GenieWorksheet]
    type_objects: List[GenieType]

    @classmethod
    def create_empty(cls) -> "ObjectCollection":
        """Create an empty collection.

        Returns:
            ObjectCollection: A new empty collection.
        """
        return cls([], [], [])


class ContextDiffer:
    """Handles comparison and diffing between contexts.

    This class is responsible for finding differences between two contexts,
    with special handling for GenieWorksheets and GenieFields.
    """

    @staticmethod
    def diff_contexts(context1: Dict, context2: Dict) -> Dict:
        """Compare two contexts and return their differences.

        Args:
            context1 (Dict): The first context.
            context2 (Dict): The second context.

        Returns:
            Dict: The differences between the contexts.
        """
        logger.debug("Starting context diff comparison")
        log_context(context1, "DEBUG")
        log_context(context2, "DEBUG")

        diff = {}
        for key, value in context2.items():
            if key not in context1:
                logger.debug(f"New key found in context2: {key}")
                diff[key] = value
            else:
                if isinstance(value, GenieWorksheet) and isinstance(
                    context1[key], GenieWorksheet
                ):
                    if not same_worksheet(value, context1[key]):
                        logger.debug(f"Worksheet difference found for {key}")
                        log_worksheet_state(value, "DEBUG")
                        log_worksheet_state(context1[key], "DEBUG")
                        diff[key] = value
                elif isinstance(value, GenieField) and isinstance(
                    context1[key], GenieField
                ):
                    if not same_field(value, context1[key]):
                        logger.debug(f"Field difference found for {key}")
                        diff[key] = value
                elif value != context1[key]:
                    logger.debug(f"Value difference found for {key}")
                    diff[key] = value

        logger.debug(f"Context diff completed. Found {len(diff)} differences")
        return diff


class ObjectDiscoverer:
    """Discovers and categorizes objects in the context.

    This class is responsible for finding and categorizing objects into
    answer objects, worksheet objects, and type objects.
    """

    @staticmethod
    def discover_objects(
        local_context: GenieContext, collection: ObjectCollection, bot: GenieRuntime
    ) -> None:
        """Find and categorize objects in the context.

        Args:
            local_context (GenieContext): The context to search.
            collection (ObjectCollection): Collection to store found objects.
            bot (GenieRuntime): The bot instance.
        """
        logger.debug("Starting object discovery in context")
        log_context(local_context.context, "DEBUG")

        for obj_name, obj in local_context.context.items():
            logger.debug(f"Processing object: {obj_name} of type {type(obj)}")

            if (
                obj in collection.answer_objects
                or obj in collection.worksheet_objects
                or obj in collection.type_objects
            ):
                logger.debug(f"Object {obj_name} already processed, skipping")
                continue

            if isinstance(obj, list):
                logger.debug(f"Processing list object: {obj_name}")
                ObjectDiscoverer._process_list_objects(obj, collection)
            if isinstance(obj, GenieWorksheet):
                logger.debug(f"Processing worksheet object: {obj_name}")
                ObjectDiscoverer._process_worksheet_object(obj, collection)

        logger.debug(f"Found {len(collection.type_objects)} type objects")
        for type_object in collection.type_objects:
            logger.debug(
                f"Processing actions for type object: {type_object.__class__.__name__}"
            )
            incoming_actions = ActionPolicyExecutor.perform_worksheet_actions(
                type_object, bot, local_context
            )
            logger.debug(f"Adding {len(incoming_actions)} new actions to bot context")
            bot.context.agent_acts.extend(incoming_actions)

    @staticmethod
    def _process_list_objects(obj_list: List, collection: ObjectCollection) -> None:
        """Process objects in a list and categorize them.

        Args:
            obj_list (List): List of objects to process.
            collection (ObjectCollection): Collection to store categorized objects.
        """
        for item in obj_list:
            if isinstance(item, GenieType) and item not in collection.type_objects:
                collection.type_objects.append(item)
            elif isinstance(item, Answer) and item not in collection.answer_objects:
                collection.answer_objects.append(item)
            elif item not in collection.worksheet_objects:
                collection.worksheet_objects.append(item)

    @staticmethod
    def _process_worksheet_object(
        obj: GenieWorksheet, collection: ObjectCollection
    ) -> None:
        """Process a worksheet object and categorize it.

        Args:
            obj (GenieWorksheet): Worksheet object to process.
            collection (ObjectCollection): Collection to store categorized objects.
        """
        if isinstance(obj, Answer) and obj not in collection.answer_objects:
            collection.answer_objects.append(obj)
        elif isinstance(obj, GenieType) and obj not in collection.type_objects:
            collection.type_objects.append(obj)
        elif obj not in collection.worksheet_objects:
            collection.worksheet_objects.append(obj)


class WorksheetManager:
    """Manages worksheet-related operations.

    This class handles operations related to worksheets, including
    finding available worksheets and managing their state.
    """

    @staticmethod
    def get_available_worksheets(
        turn_context: GenieContext, bot: GenieRuntime
    ) -> List[str]:
        """Find available worksheets that can be instantiated.

        Args:
            turn_context (GenieContext): The current turn context.
            bot (GenieRuntime): The bot instance.

        Returns:
            List[str]: List of code strings for creating new worksheet instances.
        """
        logger.debug("Starting search for available worksheets")
        if any_open_empty_ws(turn_context, bot.context):
            logger.debug("Found open empty worksheet, skipping creation")
            return []

        code_strings = []
        logger.debug(
            f"Checking {len(bot.genie_worksheets)} worksheets for availability"
        )
        for ws in bot.genie_worksheets:
            if WorksheetManager._should_create_worksheet(ws, turn_context, bot):
                logger.info(f"Creating a new instance of {ws.__name__}")
                var_name = generate_var_name(ws.__name__)
                logger.debug(f"Generated variable name: {var_name}")
                code_strings.append(f"{var_name} = {ws.__name__}()")
                break  # Only open one worksheet at a time

        logger.debug(f"Found {len(code_strings)} worksheets to create")
        return code_strings

    @staticmethod
    def _should_create_worksheet(
        ws: Type[GenieWorksheet], turn_context: GenieContext, bot: GenieRuntime
    ) -> bool:
        """Determine if a new worksheet instance should be created.

        Args:
            ws (Type[GenieWorksheet]): The worksheet class.
            turn_context (GenieContext): The current turn context.
            bot (GenieRuntime): The bot instance.

        Returns:
            bool: True if a new worksheet should be created.
        """
        logger.debug(f"Evaluating if worksheet {ws.__name__} should be created")

        # Check if worksheet already exists in turn context
        exists_in_turn = any([isinstance(x, ws) for x in turn_context.context.values()])
        if exists_in_turn:
            logger.debug(f"Worksheet {ws.__name__} already exists in turn context")
            return False

        # Check if worksheet exists in bot context
        exists_in_bot = any([isinstance(x, ws) for x in bot.context.context.values()])
        if exists_in_bot:
            logger.debug(f"Worksheet {ws.__name__} already exists in bot context")
            return False

        # Check if worksheet is a GenieType
        if issubclass(ws, GenieType):
            logger.debug(f"Worksheet {ws.__name__} is a GenieType, skipping")
            return False

        # Check predicates
        if ws.predicate:
            predicate_result = bot.eval(ws.predicate, turn_context)
            logger.debug(f"Predicate evaluation for {ws.__name__}: {predicate_result}")
            return predicate_result

        logger.debug(f"No restrictions found for {ws.__name__}, can be created")
        return True


class ActionPolicyExecutor:
    """Executes action policies for worksheets and fields.

    This class is responsible for executing actions based on policies
    for both worksheets and their fields.
    """

    @staticmethod
    def perform_field_actions(
        obj: GenieWorksheet, bot: GenieRuntime, local_context: GenieContext
    ) -> List[AgentAct]:
        """Execute actions for fields in a worksheet.

        Args:
            obj (GenieWorksheet): The worksheet object.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.

        Returns:
            List[AgentAct]: List of agent actions to perform.
        """
        logger.debug(
            f"Performing field actions for worksheet: {obj.__class__.__name__}"
        )
        log_worksheet_state(obj)
        agent_acts = []

        def perform_action(obj: GenieWorksheet) -> None:
            logger.debug(f"Processing fields for worksheet: {obj.__class__.__name__}")
            for field in get_genie_fields_from_ws(obj):
                logger.debug(
                    f"Processing field: {field.name}, value={field.value}, confirmed={field.confirmed}"
                )
                if field.value is not None:
                    if field.requires_confirmation and field.confirmed:
                        logger.debug(
                            f"Handling field that requires confirmation and is confirmed: {field.name}"
                        )
                        ActionPolicyExecutor._handle_confirmed_field(
                            field, obj, bot, local_context, agent_acts
                        )
                    elif not field.requires_confirmation:
                        logger.debug(
                            f"Handling field that does not require confirmation: {field.name}"
                        )
                        ActionPolicyExecutor._handle_unconfirmed_field(
                            field, obj, bot, local_context, agent_acts
                        )

        perform_action(obj)
        return agent_acts

    @staticmethod
    def perform_worksheet_actions(
        obj: GenieWorksheet, bot: GenieRuntime, local_context: GenieContext
    ) -> List[AgentAct]:
        """Execute actions for a worksheet.

        Args:
            obj (GenieWorksheet): The worksheet object.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.

        Returns:
            List[AgentAct]: List of agent actions to perform.
        """
        logger.debug(f"Starting worksheet actions for: {obj.__class__.__name__}")
        log_worksheet_state(obj)

        agent_acts = []
        if obj.is_complete(bot, local_context) and not obj.action_performed:
            logger.debug(
                f"Worksheet {obj.__class__.__name__} is complete and action not performed"
            )

            if hasattr(obj, "backend_api") and len(obj.backend_api):
                logger.debug(f"Executing backend API for {obj.__class__.__name__}")
                obj.execute(bot, local_context)

            logger.info(
                f"Performing Worksheet action for {obj.__class__.__name__}: {obj.actions.action}"
            )
            actions = obj.perform_action(bot, local_context)
            new_actions = [action for action in actions if isinstance(action, AgentAct)]
            logger.debug(f"Generated {len(new_actions)} new agent actions")
            agent_acts.extend(new_actions)

        logger.debug(f"Completed worksheet actions for: {obj.__class__.__name__}")
        return agent_acts

    @staticmethod
    def _handle_confirmed_field(
        field: GenieField,
        obj: GenieWorksheet,
        bot: GenieRuntime,
        local_context: GenieContext,
        agent_acts: List[AgentAct],
    ) -> None:
        """Handle actions for a confirmed field.

        Args:
            field (GenieField): The field to handle.
            obj (GenieWorksheet): The worksheet containing the field.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.
            agent_acts (List[AgentAct]): List to append actions to.
        """
        logger.debug(
            f"Handling confirmed field: {field.name} in {obj.__class__.__name__}"
        )

        if isinstance(field.value, GenieWorksheet):
            logger.debug(
                f"Field {field.name} contains a worksheet, performing field actions"
            )
            ActionPolicyExecutor.perform_field_actions(field.value, bot, local_context)
        else:
            logger.info(f"Performing action for {field.name}: {field.actions}")
            new_actions = field.perform_action(bot, local_context)
            logger.debug(
                f"Generated {len(new_actions)} new actions for field {field.name}"
            )
            agent_acts.extend(new_actions)

        log_action_result(f"confirmed_field_{field.name}", agent_acts)

    @staticmethod
    def _handle_unconfirmed_field(
        field: GenieField,
        obj: GenieWorksheet,
        bot: GenieRuntime,
        local_context: GenieContext,
        agent_acts: List[AgentAct],
    ) -> None:
        """Handle actions for an unconfirmed field.

        Args:
            field (GenieField): The field to handle.
            obj (GenieWorksheet): The worksheet containing the field.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.
            agent_acts (List[AgentAct]): List to append actions to.
        """
        logger.debug(
            f"Handling unconfirmed field: {field.name} in {obj.__class__.__name__}"
        )

        if isinstance(field.value, GenieWorksheet):
            logger.debug(
                f"Field {field.name} contains a worksheet, performing field actions"
            )
            ActionPolicyExecutor.perform_field_actions(field.value, bot, local_context)
        else:
            logger.info(f"Performing action for {field.name}: {field.actions}")
            new_actions = field.perform_action(bot, local_context)
            logger.debug(
                f"Generated {len(new_actions)} new actions for field {field.name}"
            )
            agent_acts.extend(new_actions)

        log_action_result(f"unconfirmed_field_{field.name}", agent_acts)


class QuestionPolicyManager:
    """Manages policies for asking questions and confirmations.

    This class handles the logic for determining when to ask questions
    or request confirmations from users.
    """

    @staticmethod
    def ask_question_policy(
        obj: GenieWorksheet, bot: GenieRuntime, local_context: GenieContext
    ) -> List[AgentAct]:
        """Determine which questions need to be asked.

        Args:
            obj (GenieWorksheet): The worksheet object.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.

        Returns:
            List[AgentAct]: List of agent actions for asking questions.
        """
        logger.debug(
            f"Starting question policy for worksheet: {obj.__class__.__name__}"
        )
        log_worksheet_state(obj)

        fields_to_ask = []
        already_checked = []

        def check_slots(obj: Union[GenieWorksheet, Type[GenieWorksheet]]) -> None:
            if fields_to_ask:
                logger.debug("Questions already found, skipping further checks")
                return

            obj_name = get_variable_name(obj, local_context)
            logger.debug(f"Checking slots for object: {obj_name}")

            if not any_open_empty_ws(local_context, bot.context):
                logger.debug(
                    "No open empty worksheets found, handling worksheet creation"
                )
                QuestionPolicyManager._handle_no_open_worksheet(
                    obj, obj_name, local_context, bot
                )

            if isinstance(obj, GenieWorksheet) and hasattr(obj, "_ordered_attributes"):
                logger.debug(f"Checking fields for worksheet: {obj_name}")
                QuestionPolicyManager._check_worksheet_fields(
                    obj, obj_name, fields_to_ask, already_checked, local_context, bot
                )

        check_slots(obj)

        if fields_to_ask:
            logger.debug(f"Found {len(fields_to_ask)} fields to ask questions about")
            logger.debug(f"Asking question for field: {fields_to_ask[0]['field'].name}")
            return [AskAgentAct(**fields_to_ask[0])]

        logger.debug("No questions need to be asked")
        return []

    @staticmethod
    def ask_confirmation_policy(
        obj: GenieWorksheet, bot: GenieRuntime, local_context: GenieContext
    ) -> List[AgentAct]:
        """Determine which fields need confirmation.

        Args:
            obj (GenieWorksheet): The worksheet object.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.

        Returns:
            List[AgentAct]: List of agent actions for requesting confirmations.
        """
        logger.debug(
            f"Starting confirmation policy for worksheet: {obj.__class__.__name__}"
        )
        log_worksheet_state(obj)

        # If the previous system turn asked to confirm a field and the current turn
        # cleared that same field (for example, user said "no"), prioritize re-asking
        # the field value before asking to confirm any subsequent fields.
        if QuestionPolicyManager._has_pending_rejected_confirmation(obj, bot):
            logger.debug(
                "Detected rejected confirmation with missing replacement value; "
                "skipping confirmation asks this turn."
            )
            return []

        ask_for_confirmation = []

        def check_for_confirmation(obj: GenieWorksheet) -> None:
            logger.debug(
                f"Checking fields for confirmation in: {obj.__class__.__name__}"
            )
            for field in get_genie_fields_from_ws(obj):
                logger.debug(
                    f"Checking field: {field.name}, requires_confirmation={field.requires_confirmation}, confirmed={field.confirmed}"
                )
                if field.value is None:
                    continue

                if isinstance(field.value, (GenieWorksheet, GenieType)):
                    check_for_confirmation(field.value)
                    if ask_for_confirmation:
                        return
                    continue

                if (
                    field.requires_confirmation
                    and not field.confirmed
                    and QuestionPolicyManager._field_value_has_info(field.value)
                ):
                    logger.debug(f"Field {field.name} needs confirmation")
                    ask_for_confirmation.append({"ws": obj, "field": field})
                    return

        check_for_confirmation(obj)

        if ask_for_confirmation:
            field_to_ask = ask_for_confirmation[0]
            var_name = get_variable_name(field_to_ask["ws"], local_context)
            logger.debug(
                f"Requesting confirmation for field: {field_to_ask['field'].name} in worksheet {var_name}"
            )
            return [
                AskForConfirmationAgentAct(
                    **field_to_ask,
                    ws_name=var_name,
                    field_name=f"{var_name}.{field_to_ask['field'].name}",
                )
            ]

        logger.debug("No fields need confirmation")
        return []

    @staticmethod
    def _has_pending_rejected_confirmation(
        obj: GenieWorksheet, bot: GenieRuntime
    ) -> bool:
        """Return True when the last turn asked for confirmation on a field and
        that field is now empty, indicating the user rejected the value.
        """
        recently_confirmed_field_names: set[str] = set()
        try:
            dlg = getattr(getattr(bot, "agent", None), "dlg_history", None)
            if dlg and len(dlg) > 0:
                last_sys = dlg[-1].system_action
                if last_sys is not None:
                    for act in last_sys.actions:
                        if isinstance(act, AskForConfirmationAgentAct):
                            recently_confirmed_field_names.add(act.field.name)
        except (AttributeError, IndexError):
            return False

        if not recently_confirmed_field_names:
            return False

        def _check(ws: GenieWorksheet) -> bool:
            for field in get_genie_fields_from_ws(ws):
                if isinstance(field.value, (GenieWorksheet, GenieType)) and _check(
                    field.value
                ):
                    return True

                if (
                    field.name in recently_confirmed_field_names
                    and field.requires_confirmation
                    and field.ask
                    and not field.internal
                    and field.value is None
                ):
                    return True
            return False

        return _check(obj)

    @staticmethod
    def _handle_no_open_worksheet(
        obj: Union[GenieWorksheet, Type[GenieWorksheet]],
        obj_name: str,
        local_context: GenieContext,
        bot: GenieRuntime,
    ) -> None:
        """Handle the case when there are no open worksheets.

        Args:
            obj: The worksheet object or class.
            obj_name (str): The name of the object.
            local_context (GenieContext): The local context.
            bot (GenieRuntime): The bot instance.
        """
        logger.debug(f"Handling no open worksheet for: {obj_name}")

        # Respect runtime setting – bail early if automatic worksheet creation is disabled
        if not OPEN_NEW_WORKSHEET_IF_POSSIBLE:
            logger.debug("Automatic worksheet creation disabled via config—skipping.")
            return

        if inspect.isclass(obj) and issubclass(obj, GenieWorksheet):
            logger.debug(f"Object {obj_name} is a worksheet class")
            if eval_predicates(obj.predicate, obj, bot, local_context):
                logger.debug(f"Predicates evaluated true for {obj_name}")
                var_counter = count_worksheet_variables(local_context.context)
                var_name = (
                    f"{generate_var_name(obj_name)}_{var_counter.get(generate_var_name(obj_name), 0)}"
                    if var_counter > 0
                    else generate_var_name(obj_name)
                )
                logger.debug(f"Creating new worksheet instance with name: {var_name}")
                bot.execute(f"{var_name} = {obj_name}()", local_context)
                logger.debug(f"Successfully created worksheet: {var_name}")

    @staticmethod
    def _check_worksheet_fields(
        obj: GenieWorksheet,
        obj_name: str,
        fields_to_ask: List[Dict],
        already_checked: List,
        local_context: GenieContext,
        bot: GenieRuntime,
    ) -> None:
        """Check fields in a worksheet for questions needed.

        Args:
            obj (GenieWorksheet): The worksheet object.
            obj_name (str): Name of the worksheet.
            fields_to_ask (List[Dict]): List to store fields needing questions.
            already_checked (List): List of already checked fields.
            local_context (GenieContext): The local context.
            bot (GenieRuntime): The bot instance.
        """
        logger.debug(f"Checking fields in worksheet: {obj_name}")

        for field in get_genie_fields_from_ws(obj):
            logger.debug(f"Evaluating field: {field.name}")

            if not eval_predicates(field.predicate, obj, bot, local_context):
                logger.debug(f"Field {field.name} predicates evaluated false, skipping")
                continue

            if hasattr(field.slottype, "__bases__") and (
                field.slottype.__bases__ == (GenieType,)
                or field.slottype.__bases__ == (GenieWorksheet,)
            ):
                logger.debug(f"Field {field.name} has special slot type")
                nested_obj = field.value

                # If a nested worksheet slot is unset, inspect its schema to find the
                # first askable leaf field instead of asking users about the container.
                if (
                    nested_obj is None
                    and inspect.isclass(field.slottype)
                    and issubclass(field.slottype, GenieWorksheet)
                ):
                    logger.debug(
                        f"Materializing nested worksheet field {field.name} as {field.slottype.__name__}"
                    )
                    nested_obj = field.slottype()
                    field.value = nested_obj

                    # Run field-level actions (e.g. hydration) immediately after materialization.
                    if (
                        field.actions is not None
                        and len(field.actions) > 0
                        and not field.action_performed
                    ):
                        logger.debug(
                            f"Running field action after materialization for {field.name}"
                        )
                        field.perform_action(bot, local_context)
                        nested_obj = field.value

                    # Fallback hydrator path: if a domain API named
                    # `hydrate_<field_name>` exists, invoke it directly after
                    # materialization. This avoids relying solely on action-code
                    # execution when the parser skipped explicit initialization.
                    hydrate_fn = bot.context.context.get(f"hydrate_{field.name}")
                    if callable(hydrate_fn):
                        lead_id_field = getattr(obj, "lead_id", None)
                        lead_id_value = (
                            getattr(lead_id_field, "value", lead_id_field)
                            if lead_id_field is not None
                            else None
                        )
                        try:
                            hydrate_fn(nested_obj, lead_id_value)
                        except TypeError:
                            hydrate_fn(nested_obj)

                if nested_obj is not None and nested_obj not in already_checked:
                    logger.debug(f"Checking nested value in field {field.name}")
                    already_checked.append(nested_obj)
                    QuestionPolicyManager._check_worksheet_fields(
                        nested_obj,
                        obj_name,
                        fields_to_ask,
                        already_checked,
                        local_context,
                        bot,
                    )
                    if fields_to_ask:
                        return
                    continue

            if field.value is None and not field.internal and field.ask:
                logger.debug(f"Field {field.name} needs to be asked")
                fields_to_ask.append({"ws": obj, "field": field, "ws_name": obj_name})

    @staticmethod
    def _field_value_has_info(value: Any) -> bool:
        """Check if a field value contains meaningful information.

        Args:
            value (Any): The value to check.

        Returns:
            bool: True if the value contains meaningful information.
        """
        if value is None:
            return False

        if isinstance(value, GenieValue):
            has_info = value.value is not None and len(value.value) > 0
            logger.debug(f"GenieValue has info: {has_info}")
            return has_info

        return True

    @staticmethod
    def auto_confirm_answered_fields(
        obj: GenieWorksheet,
        bot: GenieRuntime,
        local_context: GenieContext,
    ) -> List[AgentAct]:
        """Auto-confirm requires_confirmation fields that were just populated
        in direct response to an AskAgentAct (i.e. the field was empty and the
        user supplied the value).  Returns ReportAgentActs acknowledging each
        recorded value, plus any field/worksheet actions that become runnable
        because of the newly confirmed state.
        """
        prev_asked_field_names: set = set()
        try:
            dlg = getattr(getattr(bot, "agent", None), "dlg_history", None)
            if dlg and len(dlg) > 0:
                last_sys = dlg[-1].system_action
                if last_sys is not None:
                    for act in last_sys.actions:
                        if isinstance(act, AskAgentAct):
                            prev_asked_field_names.add(act.field.name)
        except (AttributeError, IndexError):
            pass

        if not prev_asked_field_names:
            return []

        acts: List[AgentAct] = []
        confirmed_worksheets: List[GenieWorksheet] = []

        def _check(ws: GenieWorksheet) -> None:
            for field in get_genie_fields_from_ws(ws):
                if (
                    field.value is not None
                    and field.requires_confirmation
                    and not field.confirmed
                    and field.name in prev_asked_field_names
                ):
                    if isinstance(field._value, GenieValue):
                        field._value.confirmed = True
                    else:
                        field._confirmed = True
                    logger.info(
                        f"Auto-confirmed field {field.name} = {field.value} "
                        f"(answered directly via AskAgentAct)"
                    )
                    acts.append(
                        ReportAgentAct(
                            None,
                            f"Recorded {field.name}: {field.value}",
                        )
                    )
                    if ws not in confirmed_worksheets:
                        confirmed_worksheets.append(ws)

                if isinstance(field.value, (GenieWorksheet, GenieType)):
                    _check(field.value)

        _check(obj)

        for ws in confirmed_worksheets:
            field_acts = ActionPolicyExecutor.perform_field_actions(
                ws, bot, local_context
            )
            acts.extend(field_acts)
            ws_acts = ActionPolicyExecutor.perform_worksheet_actions(
                ws, bot, local_context
            )
            acts.extend(ws_acts)

        return acts


class AgentPolicyManager:
    """Main class for managing and executing agent policies.

    This class orchestrates the execution of various policies and
    manages the overall policy workflow.
    """

    def __init__(self, bot: GenieRuntime):
        """Initialize the AgentPolicyManager.

        Args:
            bot (GenieRuntime): The bot runtime instance.
        """
        logger.debug("Initializing AgentPolicyManager")
        self.bot = bot
        self.context_differ = ContextDiffer()
        self.worksheet_manager = WorksheetManager()
        self.action_executor = ActionPolicyExecutor()
        self.question_manager = QuestionPolicyManager()
        logger.debug("AgentPolicyManager initialization complete")

    def run_policy(self, current_dlg_turn: CurrentDialogueTurn) -> None:
        """Execute the agent policy for the current dialogue turn.

        Args:
            current_dlg_turn (CurrentDialogueTurn): The current dialogue turn.
        """
        logger.info("Starting agent policy execution")
        user_target = current_dlg_turn.user_target or ""
        user_target_lines = user_target.split("\n")
        logger.debug(f"Processing {len(user_target_lines)} lines of user target")

        original_global_context = genie_deepcopy(self.bot.context.context)
        turn_context = GenieContext()

        # Execute user target and generate agent acts
        logger.debug("Executing user target and generating policy")
        original_global_context = self._execute_and_generate_policy(
            user_target_lines, original_global_context, turn_context
        )

        # Check for additional acts if possible
        if (
            OPEN_NEW_WORKSHEET_IF_POSSIBLE
            and self.bot.context.agent_acts.can_have_other_acts()
        ):
            logger.debug("Checking for additional available worksheets")
            code_strings = self.worksheet_manager.get_available_worksheets(
                turn_context, self.bot
            )
            if code_strings:
                logger.debug(
                    f"Found {len(code_strings)} additional worksheets to process"
                )
                self._execute_and_generate_policy(
                    code_strings, original_global_context, turn_context
                )

            self.bot.update_from_context(turn_context)

        # Update dialogue turn
        logger.debug("Updating dialogue turn with new context and actions")
        self._update_dialogue_turn(current_dlg_turn, turn_context)

        logger.info("Agent policy execution completed")
        log_context(self.bot.context.context, "DEBUG")

    def _was_permission_prompted_last_turn(self) -> bool:
        """Return True if the previous system action explicitly asked for
        `permission_to_continue`.
        """
        try:
            dlg = getattr(getattr(self.bot, "agent", None), "dlg_history", None)
            if not dlg or len(dlg) == 0:
                return False
            last_sys = dlg[-1].system_action
            if last_sys is None:
                return False
            for act in last_sys.actions:
                if (
                    isinstance(act, AskAgentAct)
                    and getattr(getattr(act, "field", None), "name", None)
                    == "permission_to_continue"
                ):
                    return True
        except (AttributeError, IndexError):
            return False
        return False

    @staticmethod
    def _get_field_value(field_or_value: Any) -> Any:
        if hasattr(field_or_value, "value"):
            return field_or_value.value
        return field_or_value

    @staticmethod
    def _set_field_value(field_or_value: Any, value: Any) -> None:
        if hasattr(field_or_value, "value"):
            field_or_value.value = value

    def _revert_unauthorized_permission_assignments(
        self, original_global_context: Dict
    ) -> None:
        """Guardrail: only allow setting permission_to_continue when the prior
        system action asked for that field.
        """
        if self._was_permission_prompted_last_turn():
            return

        for key, current_obj in self.bot.context.context.items():
            if key == "__builtins__":
                continue

            original_obj = original_global_context.get(key)

            # Direct LeadContact-like objects in context
            if hasattr(current_obj, "permission_to_continue"):
                current_perm_field = getattr(current_obj, "permission_to_continue")
                current_perm_value = self._get_field_value(current_perm_field)

                original_perm_value = None
                if original_obj is not None and hasattr(
                    original_obj, "permission_to_continue"
                ):
                    original_perm_value = self._get_field_value(
                        getattr(original_obj, "permission_to_continue")
                    )

                if current_perm_value is True and original_perm_value is not True:
                    logger.debug(
                        f"Reverting unauthorized permission_to_continue=True on {key}"
                    )
                    self._set_field_value(current_perm_field, original_perm_value)

            # Main-like objects that nest lead_contact and verify_quote
            if not hasattr(current_obj, "lead_contact"):
                continue

            current_lead_contact_field = getattr(current_obj, "lead_contact")
            current_lead_contact = self._get_field_value(current_lead_contact_field)
            if current_lead_contact is None or not hasattr(
                current_lead_contact, "permission_to_continue"
            ):
                continue

            original_lead_contact = None
            if original_obj is not None and hasattr(original_obj, "lead_contact"):
                original_lead_contact = self._get_field_value(
                    getattr(original_obj, "lead_contact")
                )

            current_perm_field = getattr(current_lead_contact, "permission_to_continue")
            current_perm_value = self._get_field_value(current_perm_field)

            original_perm_value = None
            if original_lead_contact is not None and hasattr(
                original_lead_contact, "permission_to_continue"
            ):
                original_perm_value = self._get_field_value(
                    getattr(original_lead_contact, "permission_to_continue")
                )

            if current_perm_value is True and original_perm_value is not True:
                logger.debug(
                    f"Reverting unauthorized nested permission_to_continue=True on {key}.lead_contact"
                )
                self._set_field_value(current_perm_field, original_perm_value)

                # Also prevent accidental phase advancement caused by unauthorized
                # permission assignment in the same turn.
                if hasattr(current_obj, "verify_quote"):
                    current_verify_quote_field = getattr(current_obj, "verify_quote")
                    current_verify_quote = self._get_field_value(
                        current_verify_quote_field
                    )

                    original_verify_quote = None
                    if original_obj is not None and hasattr(original_obj, "verify_quote"):
                        original_verify_quote = self._get_field_value(
                            getattr(original_obj, "verify_quote")
                        )

                    if current_verify_quote is not None and original_verify_quote is None:
                        logger.debug(
                            f"Reverting unauthorized verify_quote initialization on {key}"
                        )
                        self._set_field_value(current_verify_quote_field, None)

    def _execute_and_generate_policy(
        self,
        code_lines: List[str],
        original_global_context: Dict,
        turn_context: GenieContext,
    ) -> Dict:
        """Execute code lines and generate policy.

        Args:
            code_lines (List[str]): Lines of code to execute.
            original_global_context (Dict): The original global context.
            turn_context (GenieContext): The current turn context.

        Returns:
            Dict: New global context.
        """
        logger.debug(f"Starting policy execution for {len(code_lines)} code lines")

        for i, code_line in enumerate(code_lines):
            if not code_line:
                continue

            logger.debug(f"Processing code line {i + 1}/{len(code_lines)}: {code_line}")
            local_context = GenieContext()
            self.bot.execute(code_line, local_context, sp=True)
            self._revert_unauthorized_permission_assignments(original_global_context)

            # Get context differences and update local context
            logger.debug("Computing context differences")
            diff_context = self.context_differ.diff_contexts(
                original_global_context, self.bot.context.context
            )

            logger.debug(f"Updating local context with {len(diff_context)} differences")
            for key, value in diff_context.items():
                if key != "__builtins__" and key not in local_context.context:
                    local_context.set(key, value)

            # Discover and execute local policies
            logger.debug("Discovering and executing local policies")
            local_context = self._discover_and_execute_local(local_context)

            # completed_apis = {}
            # # Assign the result of completed Worksheet to their respective variables
            # for key, value in local_context.context.items():
            #     if (
            #         isinstance(value, GenieWorksheet)
            #         and value.is_complete(self.bot, local_context)
            #         and value.action_performed
            #         and hasattr(
            #             value, "backend_api"
            #         )  # Why should it be a backend api? This should hold true for all the worksheets
            #     ):
            #         completed_apis["__" + key] = value
            #         local_context.set(key, value.result, force=True)

            # local_context.update(completed_apis)

            # Update global context and prepare for next iteration
            logger.debug("Updating global context and turn context")
            self.bot.update_from_context(local_context)
            original_global_context = genie_deepcopy(self.bot.context.context)
            turn_context.update(local_context.context)

        # Process global policies if possible (TODO: add can act to new line for logging)
        if self.bot.context.agent_acts.can_have_other_acts():
            logger.debug("Processing global policies")
            self._discover_and_execute_global(self.bot.context)

            if self.bot.context.agent_acts.can_have_other_acts():
                logger.debug("Processing ordered policies")
                self._discover_and_execute_ordered()

        logger.debug("Policy generation complete")
        return original_global_context

    def _discover_and_execute_local(self, context: GenieContext) -> GenieContext:
        """Discover and execute policies in the local context.

        Args:
            context (GenieContext): The local context.

        Returns:
            GenieContext: The updated context.
        """
        logger.debug("Starting local policy discovery and execution")
        collection = ObjectCollection.create_empty()
        ObjectDiscoverer.discover_objects(context, collection, self.bot)

        # Process answer objects first
        logger.debug(f"Processing {len(collection.answer_objects)} answer objects")
        for answer_obj in collection.answer_objects:
            if answer_obj.is_complete(self.bot, context):
                logger.debug(
                    f"Executing complete answer object: {answer_obj.__class__.__name__}"
                )
                answer_obj.execute(self.bot, context)

        # Rediscover objects after execution
        logger.debug("Rediscovering objects after answer execution")
        collection = ObjectCollection.create_empty()
        ObjectDiscoverer.discover_objects(context, collection, self.bot)

        # Process all objects
        total_objects = len(collection.answer_objects) + len(
            collection.worksheet_objects
        )
        logger.debug(f"Processing {total_objects} total objects")

        for objects in (collection.answer_objects, collection.worksheet_objects):
            for obj in objects:
                if obj.is_complete(self.bot, context) and obj.action_performed:
                    logger.debug(f"Skipping completed object: {obj.__class__.__name__}")
                    continue

                var_name = get_variable_name(obj, context)
                logger.debug(f"Processing object: {var_name}")
                self.bot.order_of_actions.append(var_name)

                if not isinstance(obj, Answer):
                    logger.debug(f"Executing field actions for {var_name}")
                    field_actions = self.action_executor.perform_field_actions(
                        obj, self.bot, context
                    )
                    self.bot.context.agent_acts.extend(field_actions)

                    logger.debug(f"Executing worksheet actions for {var_name}")
                    worksheet_actions = self.action_executor.perform_worksheet_actions(
                        obj, self.bot, context
                    )
                    self.bot.context.agent_acts.extend(worksheet_actions)

                # Rediscover objects after actions
                logger.debug("Rediscovering objects after action execution")
                ObjectDiscoverer.discover_objects(context, collection, self.bot)

        logger.debug("Local discovery and execution completed")
        return context

    def _discover_and_execute_global(self, context: GenieContext) -> None:
        """Discover and execute policies in the global context.

        Args:
            context (GenieContext): The global context.
        """
        logger.debug("Starting global discovery and execution")
        log_context(context.context, "DEBUG")

        collection = ObjectCollection.create_empty()
        ObjectDiscoverer.discover_objects(context, collection, self.bot)

        logger.debug(
            f"Processing {len(collection.answer_objects)} answer objects and {len(collection.worksheet_objects)} worksheet objects"
        )
        for objects in (collection.answer_objects, collection.worksheet_objects):
            for obj in objects:
                if not isinstance(obj, GenieWorksheet) or obj.is_complete(
                    self.bot, context
                ):
                    logger.debug(
                        f"Skipping object {obj.__class__.__name__}: not a worksheet or already complete"
                    )
                    continue

                var_name = get_variable_name(obj, context)
                logger.debug(f"Adding {var_name} to order of actions")
                self.bot.order_of_actions.append(var_name)

                if self.bot.context.agent_acts.can_have_other_acts():
                    logger.debug(f"Auto-confirming answered fields for {var_name}")
                    auto_acts = self.question_manager.auto_confirm_answered_fields(
                        obj, self.bot, context
                    )
                    if auto_acts:
                        logger.debug(
                            f"Auto-confirm produced {len(auto_acts)} acts for {var_name}"
                        )
                        self.bot.context.agent_acts.extend(auto_acts)

                if self.bot.context.agent_acts.can_have_other_acts():
                    logger.debug(f"Checking confirmation policy for {var_name}")
                    actions = self.question_manager.ask_confirmation_policy(
                        obj, self.bot, context
                    )
                    if actions:
                        logger.debug(f"Adding {len(actions)} confirmation actions")
                        self.bot.context.agent_acts.extend(actions)

                if self.bot.context.agent_acts.can_have_other_acts():
                    logger.debug(f"Checking question policy for {var_name}")
                    actions = self.question_manager.ask_question_policy(
                        obj, self.bot, context
                    )
                    if actions:
                        logger.debug(f"Adding {len(actions)} question actions")
                        self.bot.context.agent_acts.extend(actions)

                if not self.bot.context.agent_acts.can_have_other_acts():
                    logger.debug("No more actions can be added, breaking")
                    break

        logger.debug("Global discovery and execution completed")

    def _discover_and_execute_ordered(self) -> None:
        """Execute policies in the order they were discovered."""
        logger.debug(
            f"Starting ordered execution with {len(self.bot.order_of_actions)} actions"
        )

        for var_name in reversed(self.bot.order_of_actions):
            logger.debug(f"Processing action for {var_name}")
            obj = self.bot.context.context[var_name]

            if isinstance(obj, Answer):
                logger.debug(f"Skipping Answer object {var_name}")
                continue

            if not hasattr(obj, "predicate"):
                logger.debug(
                    f"Object {var_name} has no predicate, removing from actions"
                )
                self.bot.order_of_actions.remove(var_name)
                continue

            if not eval_predicates(obj.predicate, obj, self.bot, self.bot.context):
                logger.debug(f"Predicate failed for {var_name}, removing from actions")
                self.bot.order_of_actions.remove(var_name)
                continue

            if obj.is_complete(self.bot, self.bot.context) and obj.action_performed:
                logger.debug(
                    f"Object {var_name} is complete and actions performed, removing"
                )
                self.bot.order_of_actions.remove(var_name)
                continue

            if self.bot.context.agent_acts.can_have_other_acts():
                logger.debug(f"Auto-confirming answered fields for {var_name} (ordered)")
                auto_acts = self.question_manager.auto_confirm_answered_fields(
                    obj, self.bot, self.bot.context
                )
                if auto_acts:
                    logger.debug(
                        f"Auto-confirm produced {len(auto_acts)} acts for {var_name}"
                    )
                    self.bot.context.agent_acts.extend(auto_acts)

            if self.bot.context.agent_acts.can_have_other_acts():
                logger.debug(f"Checking confirmation policy for {var_name}")
                actions = self.question_manager.ask_confirmation_policy(
                    obj, self.bot, self.bot.context
                )
                if actions:
                    logger.debug(f"Adding {len(actions)} confirmation actions")
                    self.bot.context.agent_acts.extend(actions)

            if self.bot.context.agent_acts.can_have_other_acts():
                logger.debug(f"Checking question policy for {var_name}")
                actions = self.question_manager.ask_question_policy(
                    obj, self.bot, self.bot.context
                )
                if actions:
                    logger.debug(f"Adding {len(actions)} question actions")
                    self.bot.context.agent_acts.extend(actions)

            if not self.bot.context.agent_acts.can_have_other_acts():
                logger.debug("No more actions can be added, breaking")
                break

        logger.debug("Ordered execution completed")

    def _update_dialogue_turn(
        self, current_dlg_turn: CurrentDialogueTurn, turn_context: GenieContext
    ) -> None:
        """Update the dialogue turn with current context and actions.

        Args:
            current_dlg_turn (CurrentDialogueTurn): The dialogue turn to update.
            turn_context (GenieContext): The current turn context.
        """
        logger.debug("Updating dialogue turn context")
        current_dlg_turn.context.update(genie_deepcopy(turn_context.context))
        current_dlg_turn.global_context.update(genie_deepcopy(self.bot.context.context))

        if current_dlg_turn.system_action is None:
            logger.debug("Initializing system action with current agent acts")
            current_dlg_turn.system_action = self.bot.context.agent_acts
        else:
            logger.debug("Extending existing system action with new agent acts")
            current_dlg_turn.system_action.extend(self.bot.context.agent_acts)

        logger.debug("Dialogue turn update complete")


def run_agent_policy(current_dlg_turn: CurrentDialogueTurn, bot: GenieRuntime) -> None:
    """Main entry point for running the agent policy.

    Args:
        current_dlg_turn (CurrentDialogueTurn): The current dialogue turn.
        bot (GenieRuntime): The bot runtime instance.
    """
    logger.info("Starting agent policy run")
    try:
        policy_manager = AgentPolicyManager(bot)
        policy_manager.run_policy(current_dlg_turn)
        logger.info("Agent policy run completed successfully")
    except Exception as e:
        logger.error(f"Error during agent policy execution: {str(e)}")
        raise
