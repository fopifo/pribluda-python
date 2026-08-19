"""
Приблуда на python — безопасный парсер формул спреда.
Формула — арифметическое выражение, где переменные = тикеры:
"MTLR - MTLRP", "SBER - VTBR", "A - 2*B + C", "(X + Y)/2 - Z".
Парсится через ast (НЕ eval) — разрешены только числа, имена-тикеры,
операции + - * / и скобки. Всё остальное (вызовы функций, атрибуты и т.п.)
отклоняется. Это защищает от произвольного кода в поле ввода.
"""
import ast

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def parse_formula(formula):
    """Возвращает (ast-узел, set тикеров). ValueError, если формула
    содержит что-то недопустимое."""
    formula = formula.strip()
    if not formula:
        raise ValueError("пустая формула")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"синтаксис: {e.msg}")
    tickers = set()
    _validate(tree.body, tickers)
    if not tickers:
        raise ValueError("в формуле нет ни одного тикера")
    return tree.body, tickers


def _validate(node, tickers):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return
    if isinstance(node, ast.Name):
        tickers.add(node.id.upper())
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        _validate(node.left, tickers)
        _validate(node.right, tickers)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        _validate(node.operand, tickers)
        return
    raise ValueError(f"недопустимый элемент: {ast.dump(node)}")


def eval_formula(node, values):
    """Вычисляет формулу. values: {ТИКЕР: число}. Возвращает число или None
    (деление на ноль)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id.upper()]
    if isinstance(node, ast.BinOp):
        left = eval_formula(node.left, values)
        right = eval_formula(node.right, values)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                return None
            return left / right
    if isinstance(node, ast.UnaryOp):
        val = eval_formula(node.operand, values)
        if val is None:
            return None
        return -val if isinstance(node.op, ast.USub) else val
    raise ValueError("недопустимый узел")