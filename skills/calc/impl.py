"""Deterministic arithmetic with an AST whitelist instead of eval()."""
import ast
import math
import operator

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CONST = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _CONST:
            return _CONST[node.id]
        raise ValueError("unknown name '%s'" % node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported syntax: %s" % type(node).__name__)


def run(args, ctx):
    expr = str(args.get("expression", "")).strip()
    if not expr:
        return {"ok": False, "error": "empty expression"}
    if len(expr) > 200:
        return {"ok": False, "error": "expression too long"}
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval(tree)
    except ZeroDivisionError:
        return {"ok": False, "error": "division by zero", "expression": expr}
    except SyntaxError as exc:
        return {"ok": False, "error": "syntax error: %s" % exc.msg, "expression": expr}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "expression": expr}
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {"ok": True, "expression": expr, "value": value}
