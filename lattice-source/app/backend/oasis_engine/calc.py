"""Exact arithmetic for the chat agent (the calculate tool).

Models estimate when they divide or add in their heads ("27.75%" for 27.8%, "$31K" for +31%). The
agent passes an expression here instead; the result is exact and, being a tool result, counts as a
source for the number check. Only arithmetic is evaluated: the expression is parsed, never run.
"""
from __future__ import annotations

import ast
import math
import operator
import re

MAX_EXPRESSION = 500
MAX_EXPONENT = 100
_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"round": round, "abs": abs, "min": min, "max": max, "sum": lambda *a: sum(a),
              "avg": lambda *a: sum(a) / len(a), "sqrt": math.sqrt}


class CalcError(ValueError):
    pass


def _prepare(expression: str) -> str:
    """Figures as documents write them: "1,016.5" -> 1016.5, "12%" -> (12/100), "×" -> *."""
    text = expression.strip()
    if len(text) > MAX_EXPRESSION:
        raise CalcError("expression too long")
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    text = re.sub(r"(?<=\d),(?=\d{3}\b|\d{2},\d)", "", text)  # thousands separators, Indian too (9,20,000)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", text)  # percentages
    return text


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalcError("exponent too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS
            and not node.keywords):
        return _FUNCTIONS[node.func.id](*(_eval(a) for a in node.args))
    raise CalcError("only numbers, + - * / ** %, parentheses and round/abs/min/max/sum/avg/sqrt are allowed")


def calculate(expression: str) -> str:
    """The expression's value, rounded sensibly: "412/770*100" -> "53.506494"."""
    try:
        value = _eval(ast.parse(_prepare(expression), mode="eval"))
    except CalcError:
        raise
    except ZeroDivisionError as e:
        raise CalcError("division by zero") from e
    except (SyntaxError, TypeError, ValueError, OverflowError) as e:
        raise CalcError(f"not a valid expression ({type(e).__name__})") from e
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CalcError("the result is not a finite number")
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)
