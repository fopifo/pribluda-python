"""
Приблуда на python — массовая замена interval_tolerance в ticker_settings.json.
Использование:
    python tools/set_tolerance.py 0.05 0.08   # смягчить (шаг 5 плана)
    python tools/set_tolerance.py 0.08 0.05   # откат (шаг 6 плана, если FP вспухнет)
Печатает число замен — видно, что файл реально правится.
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
P = BASE / "ticker_settings.json"

def main():
    if len(sys.argv) != 3:
        print("Использование: python tools/set_tolerance.py СТАРОЕ НОВОЕ")
        print("Пример:        python tools/set_tolerance.py 0.05 0.08")
        sys.exit(1)
    old, new = sys.argv[1], sys.argv[2]
    t = P.read_text(encoding="utf-8")
    pat = re.compile(r'"interval_tolerance":\s*' + re.escape(old))
    n = len(pat.findall(t))
    t = pat.sub(f'"interval_tolerance": {new}', t)
    P.write_text(t, encoding="utf-8")
    print(f"[set_tolerance] замен '{old}' -> '{new}': {n}")
    if n == 0:
        print("[set_tolerance] ВНИМАНИЕ: ноль замен — проверь формат/аргументы")

if __name__ == "__main__":
    main()