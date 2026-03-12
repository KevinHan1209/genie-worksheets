"""Core worksheet classes for the Genie system.

This module provides the base worksheet classes that define the structure and behavior
of worksheets in the Genie system.
"""

from __future__ import annotations

from fileinput import filename
from typing import Any, Optional, Type, TypeVar

from loguru import logger

from worksheets.core import (  # GenieContext,; GenieRuntime,
    GenieField,
    GenieResult,
    GenieValue,
)
from worksheets.core.agent_acts import AskAgentAct, ReportAgentAct
from worksheets.utils.code_execution import (
    execute_query,
    modify_action_code,
    rewrite_action_code,
    sanitize_dev_code,
)
from worksheets.utils.field import get_genie_fields_from_ws
from worksheets.utils.predicates import eval_predicates
from worksheets.utils.variable import camel_to_snake, get_variable_name

T = TypeVar("T", bound="GenieWorksheet")


class Action:
    """Class for managing worksheet actions.

    This class handles action definition, execution, and result management
    for worksheet operations.

    Attributes:
        action: The action to perform.
    """

    def __init__(self, action):
        self.action = action

    def __len__(self):
        return len(self.action)

    def perform(
        self, obj: GenieWorksheet, bot: "GenieRuntime", local_context: "GenieContext"
    ) -> list:
        """Perform the action with the given context.

        Args:
            obj (GenieWorksheet): The worksheet object.
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context.

        Returns:
            list: List of actions performed.
        """
        code = modify_action_code(self.action, obj, bot, local_context)
        code = sanitize_dev_code(code, bot.get_all_variables())

        # this is right now a hack. We are just going to assign all the statments to a variable
        # and then return the variable
        acts = []
        # here what i need to do is:
        # 1. rewrite the code such that and inbuilt function appends its result to __return
        # 2. then return __return

        # We append the results to the __return variable. This is done by the rewriter
        transformed_code = rewrite_action_code(
            code,
            ["say", "propose", "answer_clarification_question"],  # predefined actions
        )
        code_ = f"__return = []\n{transformed_code}"

        local_context.context["__return"] = None

        # Execute the action code
        bot.execute(code_, local_context)

        # Context management
        if local_context.context["__return"] is not None:
            logger.debug(f"__return: {local_context.context['__return']}")
            acts.extend(local_context.context["__return"])
        del local_context.context["__return"]

        if "_obj" in local_context.context:
            del local_context.context["_obj"]

        return acts


def find_genie_type(cls_or_obj):
    """
    Return the GenieType base class if present, else None.

    Accepts either a class object or an instance.
    """
    import inspect

    # Normalize to a class object
    cls = cls_or_obj if inspect.isclass(cls_or_obj) else type(cls_or_obj)

    # Look for a base whose name is exactly 'GenieType'
    return next(
        (base for base in inspect.getmro(cls) if base.__name__ == "GenieType"),
        None,  # ← default returned when not found
    )


class GenieREPR(type):
    """A metaclass to customize string representation of Genie classes.

    This metaclass provides custom string representation for classes that use it,
    maintaining ordered attributes and generating schema representations.

    Attributes:
        _ordered_attributes: List of ordered attribute names for the class
    """

    def __new__(cls, name: str, bases: tuple, dct: dict) -> Type:
        """Create a new class with ordered attributes.

        Args:
            name: The class name
            bases: Base classes
            dct: Class dictionary

        Returns:
            The new class type
        """
        new_class = super().__new__(cls, name, bases, dct)
        # Store ordered attributes for question ordering
        new_class._ordered_attributes = [k for k in dct if not k.startswith("__")]
        return new_class

    def __repr__(cls) -> str:
        """Generate string representation with parameters."""
        parameters = []
        for field in get_genie_fields_from_ws(cls):
            parameters.append(field.schema(value=False))
        return f"{cls.__name__}({', '.join([param for param in parameters])})"

    def get_semantic_parser_schema(cls, db: bool = False) -> str:
        """Generate schema representation for semantic parsing.

        Returns:
            Schema string for semantic parsing
        """
        parameters = []

        # --- option 1: grab GenieType directly -----------------------
        is_genie_type = find_genie_type(cls)
        if is_genie_type:
            is_genie_type = True
        else:
            is_genie_type = False

        if is_genie_type:
            return f"GenieType: {cls.__name__}"

        for field in get_genie_fields_from_ws(cls):
            if not field.internal:
                # 1) get the "name: type" bit (no quotes)
                schema_str = field.schema(value=False)
                # 2) pull out the human-readable description
                description = field.description or ""
                # 3) build a line like "    full_name: str  # The user's full name"
                parameters.append(f"    {schema_str},  # {description}")

        if db:
            return str(cls.__name__)

        if len(parameters) == 0:
            return f"{cls.__name__}()"
        # join them with commas and newlines, wrap in the class name
        return f"{cls.__name__}(\n" + "\n".join(parameters) + "\n)"


class GenieWorksheet(metaclass=GenieREPR):
    """Base class for Genie worksheets.

    This class provides the foundation for defining worksheets with fields,
    actions, and state management. It handles initialization, field management,
    and action execution.

    Attributes:
        action_performed (bool): Whether worksheet actions have been executed
        result: The result of worksheet execution
        random_id (int): Unique identifier for the worksheet instance
    """

    def __init__(self, **kwargs: Any):
        """Initialize a worksheet instance.

        Args:
            **kwargs: Field values to initialize
        """
        self.action_performed = False
        self.result = None
        self.random_id = 0

        # Initialize fields from class definition
        for attr_name, attr_value in self.__class__.__dict__.items():
            if isinstance(attr_value, GenieField):
                params = {
                    field: getattr(attr_value, field)
                    for field in dir(attr_value)
                    if not field.startswith("__")
                }

                # Set user-provided values
                if attr_name in kwargs:
                    params["value"] = kwargs[attr_name]
                    if params["value"] == "":
                        params["value"] = None

                if "optional" in params:
                    if not params["optional"] and params["value"] == "NA":
                        params["value"] = None

                field_instance = GenieField(**params)
                # Rebind field parent to this worksheet instance so field actions
                # execute against live per-conversation state (not class state).
                field_instance.parent = self
                setattr(self, attr_name, field_instance)

    def perform_action(self, bot: Any, local_context: Any) -> list:
        """Perform the action associated with this worksheet.

        Args:
            bot: The bot instance
            local_context: The local context for the action

        Returns:
            List of actions performed
        """
        if self.action_performed:
            return []

        if not hasattr(self, "actions") or not self.actions:
            return []

        acts = self.actions.perform(self, bot, local_context)
        self.action_performed = True
        return acts

    def is_complete(self, bot: Any, context: Any) -> bool:
        """Check if the worksheet is complete.

        A worksheet is complete when all required fields are filled and confirmed.

        Args:
            bot: The bot instance
            context: The context for evaluation

        Returns:
            True if complete, False otherwise
        """
        for field in get_genie_fields_from_ws(self):
            if eval_predicates(field.predicate, self, bot, context):
                if isinstance(field.value, GenieWorksheet):
                    if not field.value.is_complete(bot, context):
                        return False
                if (field.value is None or field.value == "") and not field.optional:
                    return False
                if field.requires_confirmation and not field.confirmed:
                    return False
        return True

    def __repr__(self) -> str:
        """Generate string representation with field values."""
        parameters = []
        for field in get_genie_fields_from_ws(self):
            if isinstance(field, GenieField):
                parameters.append(field)
        return f"{self.__class__.__name__}({', '.join([repr(param) for param in parameters])})"

    def schema_without_type(self, context: Any) -> str:
        """Generate schema representation without type information.

        Args:
            context: The context for schema generation

        Returns:
            Schema string without type information
        """
        parameters = []
        for field in get_genie_fields_from_ws(self):
            if field.value is None:
                continue
            if isinstance(field.value, str) and field.value == "":
                continue

            if isinstance(field.value, str):
                if field.confirmed:
                    parameters.append(f"{field.name} = confirmed({repr(field.value)})")
                else:
                    parameters.append(f"{field.name} = {repr(field.value)}")
            elif isinstance(field._value, GenieResult):
                result_str = self._format_result_value(field, context)
                if result_str:
                    parameters.append(f"{field.name} = {result_str}")
            elif isinstance(field.value, GenieWorksheet):
                var_name = get_variable_name(field.value, context)

                if isinstance(var_name, str):
                    if field.confirmed:
                        parameters.append(f"{field.name} = confirmed({repr(var_name)})")
                    else:
                        parameters.append(f"{field.name} = {var_name}")
                else:
                    val = field.schema_without_type(no_none=True)
                    if val:
                        parameters.append(val)
            else:
                val = field.schema_without_type(no_none=True)
                if val:
                    parameters.append(val)

        return f"{self.__class__.__name__}({', '.join([str(param) for param in parameters])})"

    def _format_result_value(self, field: GenieField, context: Any) -> Optional[str]:
        """Format a result value for schema representation.

        Args:
            field: The field containing the result
            context: The context for formatting

        Returns:
            Formatted string representation or None
        """
        if isinstance(field.value, list):
            from worksheets.utils.list_processing import process_list_result

            return str(process_list_result(field.value, context))
        return str(field.value)

    def execute(self, bot: Any, local_context: Any):
        """Execute the worksheet's backend API.

        Args:
            bot: The bot instance
            local_context: The local context for execution
        """
        if not hasattr(self, "backend_api"):
            return

        # parameters = []
        # for f in get_genie_fields_from_ws(self):
        #     parameters.append(f"{f.name}= self.{f.name}")

        # code = f"{self.backend_api}({', '.join(parameters)})"
        code = self.backend_api

        from worksheets.utils.variable import get_variable_name

        var_name = get_variable_name(self, local_context)

        from worksheets.utils.code_execution import execute_query

        result = execute_query(code, self, bot, local_context)

        self.result = GenieResult(result, self, var_name)
        bot.context.agent_acts.add(
            ReportAgentAct(code, self.result, None, f"{var_name}.result")
        )
        self.action_performed = True

    @classmethod
    def new(cls: Type[T], initialize_from_dict: dict) -> T:
        """Create a new worksheet instance from a dictionary.

        Args:
            initialize_from_dict: Dictionary of field values

        Returns:
            New worksheet instance
        """
        return cls(**initialize_from_dict)

    def __setattr__(self, name: str, value: Any):
        """Set an attribute value with special handling for fields.

        Args:
            name: Attribute name
            value: Value to set
        """
        if hasattr(self, name):
            attr = getattr(self, name)
            if isinstance(attr, GenieField):
                self.action_performed = False

                # Reset confirmation fields
                for field in get_genie_fields_from_ws(self):
                    if field.slottype == "confirm" and field.value is True:
                        field.value = False

                if isinstance(value, GenieField) and value.name == name:
                    value.parent = self
                    super().__setattr__(name, value)
                    return

                if isinstance(value, GenieValue):
                    attr.value = value
                else:
                    attr.value = GenieValue(value)
                return
        super().__setattr__(name, value)

    def ask(self):
        """Request the system to ask questions about this worksheet.

        This increments random_id to trigger natural question asking.
        """
        logger.info(f"Ask: {self}")
        self.random_id += 1


class GenieType(GenieWorksheet):
    """Base class for Genie type definitions.

    This class extends GenieWorksheet to provide type-specific functionality
    and validation.

    Attributes:
        _parent: Parent object reference
    """

    def __init__(self, **kwargs: Any):
        """Initialize a type instance.

        Args:
            **kwargs: Field values to initialize
        """
        super().__init__(**kwargs)
        self._parent = None

    def is_complete(self, *args: Any, **kwargs: Any) -> bool:
        """Check if the type instance is complete.

        A type is complete if any primary key field is filled.

        Returns:
            True if complete, False otherwise
        """
        for field in get_genie_fields_from_ws(self):
            if field.primary_key and field.value is not None:
                return True
        return False


class GenieDB(GenieWorksheet):
    """Base class for Genie database models.

    This class extends GenieWorksheet to provide database-specific functionality
    and schema management.
    """

    pass


class Answer(GenieWorksheet):
    """Class representing an answer in the Genie system.

    This class handles query execution, result management, and parameter tracking
    for answers to user queries.

    Attributes:
        query (GenieField): The query to execute.
        actions: Associated actions.
        result: Query execution result.
        tables: Related database tables.
        potential_outputs: Possible output types.
        nl_query: Natural language query.
        param_names: Required parameter names.
    """

    def __init__(self, query, required_params, tables, nl_query, datatype=None):
        self.query = GenieField("str", "query", value=query)
        self.actions = Action(">suql_runner(self.query.value, self.required_columns)")
        self.result = None
        self.tables = tables
        self.potential_outputs = []
        self.nl_query = nl_query
        self.param_names = []
        self.action_performed = False
        self.datatype = datatype

        # find the datatype (if its primitive then, okay, else find in the context)
        if datatype is not None:
            # Define primitive types that don't need context lookup
            primitive_types = {
                "str",
                "int",
                "float",
                "bool",
                "list",
                "dict",
                "tuple",
                "set",
                "frozenset",
                "bytes",
                "bytearray",
                "complex",
                "object",
                "type",
                "None",
            }

            # If datatype is a string, check if it's primitive or needs context lookup
            if isinstance(datatype, str):
                if datatype.lower() not in primitive_types:
                    # Look up the datatype in the bot's context
                    if hasattr(Answer, "bot") and Answer.bot is not None:
                        if datatype in Answer.bot.context.context:
                            # Found the class in context, use the actual class
                            self.datatype = Answer.bot.context.context[datatype]
                        else:
                            # Not found in context, keep as string for now
                            logger.warning(
                                f"Datatype '{datatype}' not found in bot context, keeping as string"
                            )
                            self.datatype = datatype
                    else:
                        # Bot not available yet, keep as string
                        logger.debug(
                            f"Bot not available for datatype lookup, keeping '{datatype}' as string"
                        )
                        self.datatype = datatype
                # else: datatype is primitive, keep as-is
            # else: datatype is already a class object, keep as-is

        for table in self.tables:
            self.potential_outputs.extend(self.bot.context.context[table].outputs)

        self.required_columns = [
            field.name
            for table in self.tables
            for field in get_genie_fields_from_ws(self.bot.context.context[table])
        ]

        # Create required params and add them to ordered attributes
        _ordered_attributes = ["query"]
        if required_params is not None:
            for db_name, params in required_params.items():
                for param in params:
                    setattr(
                        self,
                        f"{db_name}_{param}",
                        GenieField("str", f"{db_name}.{param}", value=None),
                    )
                    self.param_names.append(f"{db_name}_{param}")
                    _ordered_attributes.append(f"{db_name}_{param}")

        self._ordered_attributes = _ordered_attributes

    def execute(self, bot: "GenieRuntime", local_context: "GenieContext"):
        """Execute the actions associated with this answer.

        Args:
            bot (GenieRuntime): The bot instance.
            local_context (GenieContext): The local context for the execution.
        """
        if self.action_performed:
            return

        results = execute_query(self.actions.action, self, bot, local_context)

        self.action_performed = True
        if results is None:
            results = []

        # Get more information about the fields
        # ws, field_name, more_field_info_result = self.more_field_info_query(bot)
        # logger.info(f"More Field Info: {more_field_info_result}")
        logger.info(f"Results: {results}")

        # Earlier we had a mechanism to check if the user is asking to execute a query or asking for more information
        # about the field. Hence we have output_idx.
        # For now we are going to assume that the user is asking for the output of the query
        output_idx = [1]

        if len(output_idx):
            output_idx = int(output_idx[0])
            if output_idx == 1:
                # Check if the output type is in the results
                output = self.output_in_result(results)
                var_name = get_variable_name(self, local_context)
                self.result = GenieResult(output, self, var_name)

                # Report the agent act
                self.bot.context.agent_acts.add(
                    ReportAgentAct(
                        self.query, self.result, var_name, var_name + ".result"
                    )
                )
                for i, _output in enumerate(output):
                    if isinstance(_output, GenieWorksheet):
                        # add the output to the local context
                        local_context.set(
                            f"{camel_to_snake(_output.__class__.__name__)}", _output
                        )
            elif output_idx == 2:
                # We don't use this for now but we can use it to ask for more information
                var_name = get_variable_name(self, local_context)
                self.result = GenieResult(more_field_info_result, self, var_name)
                self.bot.context.agent_acts.add(
                    ReportAgentAct(
                        f"AskClarificationQuestion({ws.__class__.__name__}, {filename.name})",
                        self.result,
                        message_var_name=var_name + ".result",
                    )
                )

        # for i, _output in enumerate(output):
        #     local_context.context[f"__{var_name}_result_{i}"] = _output

    def more_field_info_query(self, bot: "GenieRuntime"):
        if bot.dlg_history is None or len(bot.dlg_history) == 0:
            return None, None, None
        if bot.dlg_history[-1].system_action is None:
            return None, None, None
        acts = bot.dlg_history[-1].system_action.actions
        for act in acts:
            if isinstance(act, AskAgentAct):
                from worksheets.core.builtin_functions import generate_clarification

                more_field_info = generate_clarification(act.ws, act.field.name)
                if more_field_info:
                    return act.ws, act.field, more_field_info

        return None, None, None

    def output_in_result(self, results: list):
        """Check if the output type is in the results."""

        # we don not use the datatype for now
        def sanitize_key(key: str):
            return key.replace(" ", "_").replace("'", "").replace("&", "and").lower()

        # always fallback to the potential outputs
        if len(self.potential_outputs):
            output_results = []
            # the results is a list of dict with column and values, each element in thelist is a row
            for result in results:
                # there could be multiple types of outputs defined in geniews
                for output_type in [self.datatype, *self.potential_outputs]:
                    found_primary_key = False
                    for field in get_genie_fields_from_ws(output_type):
                        if field.primary_key and field.name in result:
                            params = {}
                            for key, value in result.items():
                                if field.name == sanitize_key(key):
                                    params[field.name] = value
                            output_results.append(output_type(**params))
                            found_primary_key = True
                            break
                    if not found_primary_key:
                        output_results.append(result)

            return output_results
        return results

    def update(self, query, unfilled_params, tables, query_str):
        """Update the answer with new parameters and tables.

        We are not using this method anymore, but we can keep it for reference."""
        logger.error(f"Updating Answer: {query}, This has been deprecated")
        self.query.value = query
        for param in self.param_names:
            del self.__dict__[param]
            self._ordered_attributes.remove(param)
        self.param_names = []
        self.required_columns = [
            field.name
            for table in tables
            for field in get_genie_fields_from_ws(self.bot.context.context[table])
        ]
        self.tables = tables
        self.potential_outputs = []
        for table in self.tables:
            self.potential_outputs.extend(self.bot.context.context[table].outputs)

        if unfilled_params is not None:
            for db_name, params in unfilled_params.items():
                for param in params:
                    setattr(
                        self,
                        f"{db_name}_{param}",
                        GenieField("str", f"{db_name}.{param}", value=None),
                    )
                    self.param_names.append(f"{db_name}_{param}")
                    self._ordered_attributes.append(f"{db_name}_{param}")

        self.nl_query = query_str


class MoreFieldInfo(GenieWorksheet):
    """Class for managing additional field information requests.

    This class handles requests for clarification or additional information
    about specific fields.

    Attributes:
        api_name (GenieField): Name of the API.
        parameter_name (GenieField): Name of the parameter.
        actions: Associated actions.
    """

    def __init__(self, api_name, parameter_name):
        self.api_name = GenieField("str", "api_name", value=api_name)
        self.parameter_name = GenieField("str", "parameter_name", value=parameter_name)
        self.actions = Action(
            ">answer_clarification_question(self.api_name, self.parameter_name)"
        )
        self.action_performed = False
