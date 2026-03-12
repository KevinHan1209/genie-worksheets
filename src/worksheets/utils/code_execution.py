"""Code execution utilities for the Genie system.

This module provides functions for executing code in the Genie system,
including code modification, action handling, and query execution.
"""

import ast
import re
import tokenize
from typing import Any, List, Tuple, Union

from loguru import logger
from pygments.lexers.python import PythonLexer
from pygments.token import Token

from worksheets.core.fields import GenieField
from worksheets.utils.field import variable_resolver
from worksheets.utils.logging_config import log_action_result, log_code_execution


def execute_query(code: str, obj: Any, bot: Any, local_context: Any) -> Any:
    """Execute a query in the given context.

    Args:
        code: The code to execute
        obj: The worksheet object
        bot: The bot instance
        local_context: The local context

    Returns:
        The result of the query execution
    """
    logger.debug(f"Starting query execution with code: {code}")

    try:
        code = modify_action_code(code, obj, bot, local_context)
        code = sanitize_dev_code(code, bot.get_all_variables())
        logger.debug(f"Modified code: {code}")

        code_ = f"__return = {code}"
        local_context.context["__return"] = None

        log_code_execution(code_, local_context.context)

        bot.execute(code_, local_context)
        logger.debug("Code execution completed successfully")

        if "_obj" in local_context.context:
            logger.debug("Cleaning up _obj from context")
            del local_context.context["_obj"]

        result = local_context.context["__return"]
        del local_context.context["__return"]

        log_action_result("query_execution", result)
        return result

    except Exception as e:
        logger.error(f"Error during query execution: {str(e)}")
        raise


def modify_action_code(code: str, obj: Any, bot: Any, local_context: Any) -> str:
    """Modify action code for execution.

    This function processes code to handle decorators, built-in functions,
    and self references correctly.

    Args:
        code: The code to modify
        obj: The object context
        bot: The bot instance
        local_context: The local context

    Returns:
        The modified code
    """
    logger.debug(f"Starting code modification for: {code}")

    api_pattern = r"@(\w+)\((.*?)\)"
    api_matches = re.findall(api_pattern, code)
    logger.debug(f"Found API matches: {api_matches}")

    inbuilt_pattern = r">(\w+)\((.*?)\)"
    inbuilt_matches = re.findall(inbuilt_pattern, code)
    logger.debug(f"Found inbuilt matches: {inbuilt_matches}")

    code = _replace_self_references(code, obj, local_context)
    logger.debug(f"Code after self reference replacement: {code}")

    code = _replace_function_calls(code, api_matches, "@", bot, local_context)
    logger.debug(f"Code after API function replacement: {code}")

    code = _replace_function_calls(code, inbuilt_matches, ">", bot, local_context)
    logger.debug(f"Code after inbuilt function replacement: {code}")

    return code


def _replace_self_references(code: str, obj: Any, local_context: Any) -> str:
    """Replace self references with the appropriate object reference.

    Args:
        code: The code containing self references
        obj: The object context
        local_context: The local context

    Returns:
        Code with replaced self references
    """
    logger.debug("Starting self reference replacement")

    if hasattr(obj, "_ordered_attributes"):
        logger.debug("Adding object to context with _ordered_attributes")
        local_context.context["_obj"] = obj
    elif hasattr(obj, "parent"):
        logger.debug("Adding parent object to context")
        local_context.context["_obj"] = obj.parent

    modified_code = code.replace("self.", "_obj.")
    modified_code = re.sub(r"self$", "_obj", modified_code)
    modified_code = re.sub(r"self}", "_obj" + "}", modified_code)

    logger.debug(f"Self reference replacement complete: {modified_code}")
    return modified_code


def _replace_function_calls(
    code: str,
    matches: List[Tuple[str, str]],
    sign: str,
    bot: Any,
    local_context: Any,
) -> str:
    """Replace function calls with their direct equivalents.

    Args:
        code: The code containing function calls
        matches: List of function matches
        sign: The sign to replace (@, >, etc.)
        bot: The bot instance
        local_context: The local context

    Returns:
        Code with replaced function calls
    """
    logger.debug(f"Starting function call replacement with sign: {sign}")
    logger.debug(f"Function matches to process: {matches}")

    for func_name, args in matches:
        if (
            func_name not in bot.context.context
            and func_name not in local_context.context
        ):
            logger.warning(f"Function {func_name} not found in context, skipping")
            continue

        logger.debug(f"Replacing function call: {sign}{func_name}")
        code = re.sub(f"{sign}{func_name}", func_name, code)

    logger.debug(f"Function call replacement complete: {code}")
    return code


def sanitize_dev_code(code: str, all_variables: List[str]) -> str:
    """Sanitize developer code to ensure it doesn't contain undefined variables.

    This function processes code to handle variable references correctly.

    Args:
        code: The code to sanitize
        all_variables: List of all valid variable names

    Returns:
        The sanitized code
    """
    logger.debug(f"Sanitizing code: {code}")
    logger.debug(f"Available variables: {all_variables}")

    try:
        lexer = PythonLexer()
        tokens = lexer.get_tokens(code)
        new_tokens_list = []

        for token in tokens:
            if token[0] == Token.Name and token[1] in all_variables:
                logger.debug(f"Adding .value to variable: {token[1]}")
                new_tokens_list.append((Token.Name, token[1] + ".value"))
            else:
                new_tokens_list.append(token)

        result = tokenize.untokenize(new_tokens_list)
        # `sanitize_dev_code` appends `.value` to known worksheet variables. For
        # GenieField metadata access like `.confirmed`, that rewrite is invalid
        # (`field.value.confirmed`) because `.value` is a primitive.
        result = re.sub(
            r"\.value\.(confirmed|requires_confirmation|ask|optional|internal|action_performed)\b",
            r".\1",
            result,
        )
        logger.debug(f"Sanitized code: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sanitizing code: {str(e)}")
        return code


def rewrite_action_code(code: str, builtin_funcs: List[str]) -> str:
    """Rewrite action code to handle built-in functions.

    This function transforms code to properly handle built-in function calls
    by appending their results to a return list.

    Args:
        code: The code to rewrite
        builtin_funcs: List of built-in function names

    Returns:
        The rewritten code
    """
    logger.debug(f"Rewriting action code: {code}")
    logger.debug(f"Built-in functions: {builtin_funcs}")

    class CallTransformer(ast.NodeTransformer):
        """AST transformer for function calls."""

        def __init__(self, builtin_funcs: List[str]):
            super().__init__()
            self.builtins = builtin_funcs

        def visit_Call(self, node: ast.Call) -> Union[ast.Call, ast.Expr]:
            """Visit a function call node and transform if needed.

            Args:
                node: The AST node to visit

            Returns:
                The transformed node
            """
            self.generic_visit(node)

            if isinstance(node.func, ast.Name) and node.func.id in self.builtins:
                # if isinstance(node.func, ast.Name):
                logger.debug(f"Transforming built-in function call: {node.func.id}")
                return ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id="__return", ctx=ast.Load()),
                            attr="append",
                            ctx=ast.Load(),
                        ),
                        args=[node],
                        keywords=[],
                    )
                )
            return node

    try:
        tree = ast.parse(code)
        transformer = CallTransformer(builtin_funcs)
        transformed_tree = transformer.visit(tree)
        result = ast.unparse(ast.fix_missing_locations(transformed_tree))
        logger.debug(f"Rewritten code: {result}")
        return result
    except Exception as e:
        logger.error(f"Error rewriting action code: {str(e)}")
        return code


def replace_undefined_variables(
    code: str, local_context: "GenieContext", global_context: "GenieContext"
):
    """Replace undefined variables in the code with their corresponding values from the context."""

    class ReplaceVariables(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in local_context.context:
                if isinstance(local_context.context[node.id], GenieField):
                    if node.id.endswith(".value"):
                        name = node.id
                    else:
                        name = node.id + ".value"
                    return ast.copy_location(
                        ast.Name(
                            id=name,
                            ctx=node.ctx,
                        ),
                        node,
                    )
            elif node.id in global_context.context:
                if isinstance(global_context.context[node.id], GenieField):
                    if node.id.endswith(".value"):
                        name = node.id
                    else:
                        name = node.id + ".value"
                    return ast.copy_location(
                        ast.Name(
                            id=name,
                            ctx=node.ctx,
                        ),
                        node,
                    )
            else:
                replacement_var = variable_resolver(
                    node.id, global_context, local_context
                )
                if replacement_var:
                    if replacement_var.endswith(".value"):
                        name = replacement_var
                    else:
                        name = replacement_var + ".value"
                    return ast.copy_location(
                        ast.Name(
                            id=name,
                            ctx=node.ctx,
                        ),
                        node,
                    )
            return node

    # Parse the code into an AST
    tree = ast.parse(code)

    # Modify the AST
    tree = ReplaceVariables().visit(tree)

    # Convert back to source code
    code = ast.unparse(tree)
    code = code.replace(".value.value", ".value")
    return code
