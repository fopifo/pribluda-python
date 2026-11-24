"""
tools/inventory.py — ИНВЕНТАРИЗАТОР проекта (шаг 1 правила ревизии).

Только ЧИТАЕТ. Ничего не удаляет, не меняет, не перемещает.

Собирает по каждому файлу проекта:
  - путь, размер, возраст (дней с изменения);
  - ТИП (боевой / инструмент / зонд / исследование / конфиг / мост / тест);
  - КТО ССЫЛАЕТСЯ: поиск имени модуля/файла по всем текстовым файлам
    проекта (импорты, батники, документация, вызовы subprocess);
  - ЗАПУСК В БАТНИКЕ: упоминается ли в *.bat;
  - QUIK-СЛЕД: ссылается ли на удалённый QUIK (quik_trades/limits/quotes, .lua).

Крупные папки data/ и output/ НЕ перечисляются пофайлово (сотни лент) —
они агрегируются по маскам с числом файлов и суммарным размером.

Выход:
  - полный отчёт: output/inventory_YYYY-MM-DD_HH-MM-SS.txt
  - краткая сводка в консоль (её и присылать в чат).

Запуск (из корня проекта):
    python tools/inventory.py

Прогресс-бар ASCII с % / позицией / скоростью / ETA (правило 2026-08-26).
"""
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "output"
MSK = ZoneInfo("Europe/Moscow")

# Папки, которые не обходим вообще (кэш, сборки, бинарники, бэкапы)
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "venv", ".venv",
    "node_modules", ".idea", ".vscode", "backups", "parts",
    "nupkg", "obj", "bin", "_Debug", "certs",
}

# Папки, которые агрегируем (не листаем пофайлово в отчёте)
AGG_DIRS = {"data", "output"}

TEXT_EXT = {
    ".py", ".md", ".txt", ".json", ".bat", ".cs", ".csproj", ".sln",
    ".lua", ".ini", ".cfg", ".yml", ".yaml", ".js", ".config",
}

CODE_EXT = {".py", ".cs"}

ENTRYPOINTS = {"main.py", "bot.py", "agent.py"}

QUIK_MARKERS = ("quik_trades", "quik_limits", "quik_quotes", ".lua")

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TICKER_DAY_RE = re.compile(r"^[A-Z0-9]+_\d{4}-\d{2}-\d{2}\.json$")


class Bar:
    """ASCII прогресс-бар: % | позиция | скорость | ETA."""

    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.n = 0
        self.t0 = time.time()
        self.last = 0.0
        self.tty = sys.stdout.isatty()
        self.lastpct = -1

    def add(self, k=1):
        self.n += k
        now = time.time()
        if now - self.last < 0.2 and self.n < self.total:
            return
        self.last = now
        self._render()

    def _render(self):
        pct = 100.0 * self.n / self.total
        filled = int(pct // 5)
        el = max(time.time() - self.t0, 1e-9)
        sp = self.n / el
        eta = (self.total - self.n) / sp if sp > 0 else 0
        line = (f"[{self.label}] [{'#' * filled}{'.' * (20 - filled)}] "
                f"{pct:3.0f}% | {self.n}/{self.total} | {sp:.0f}/с | ETA {eta:.0f}с")
        if self.tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        else:
            if int(pct) // 10 != self.lastpct // 10:
                print(line, flush=True)
            self.lastpct = int(pct)

    def done(self):
        self.n = self.total
        self._render()
        if self.tty:
            sys.stdout.write("\n")
        else:
            print(flush=True)


def human(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}ТБ"


def skipped(path):
    return any(part in SKIP_DIRS for part in path.relative_to(BASE).parts)


def data_group(name):
    """Маска для файла из data/."""
    if name.startswith("live_trades_"):
        return "live_trades_*        Алор, накопитель дня"
    if name.startswith("pbsdk_trades_"):
        return "pbsdk_trades_*       мост, лента"
    if name.startswith("orderbook_"):
        return "orderbook_*          мост, стакан"
    if name.startswith("aniscan_robots_"):
        return "aniscan_robots_*     эталон Aniscan (по дням)"
    if name.startswith("aniscan_live"):
        return "aniscan_live*        эталон Aniscan (live)"
    if name.startswith("aniscan_history"):
        return "aniscan_history*     эталон Aniscan (общий)"
    if name.startswith("aniscan_sample"):
        return "aniscan_sample*      зонд Aniscan"
    if name.startswith("tw_robots_"):
        return "tw_robots_*          эталон T-Widgets"
    if name.startswith("competitor_history"):
        return "competitor_history*  эталон конкурента"
    if name.startswith("bridge_state"):
        return "bridge_state*        состояние моста"
    if name.startswith("trade_commands"):
        return "trade_commands*      журнал торговых команд"
    if name.startswith("spoof_live"):
        return "spoof_live*          журнал мигалок (отключены)"
    if name.endswith("_history.jsonl"):
        return "*_history.jsonl      наши истории детекторов"
    if TICKER_DAY_RE.match(name):
        return "{TICKER}_{DATE}.json лента дня (Алор/конвертер)"
    if name.startswith("probe_"):
        return "probe_*              данные зондов"
    if name.startswith("tp_"):
        return "tp_*                 дампы TP для A/B"
    if name.startswith("replay_"):
        return "replay_*             истории реплеев"
    return "прочее"


def output_group(name):
    """Маска для файла из output/."""
    stem = DATE_RE.sub("<дата>", name)
    stem = re.sub(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", "<штамп>", stem)
    stem = re.sub(r"_\d{6,}", "_<штамп>", stem)
    return stem


def categorize(rel, ref_count, in_bat):
    parts = rel.parts
    name = rel.name
    top = parts[0] if len(parts) > 1 else ""
    if name in ENTRYPOINTS:
        return "ТОЧКА ВХОДА"
    if in_bat:
        return "БОЕВОЙ (батник)"
    if top in ("core", "gui", "detectors", "modules", "connectors", "integrations"):
        return "БОЕВОЙ" if ref_count else "БОЕВОЙ? (0 ссылок)"
    if top == "tests":
        return "ТЕСТ"
    if top == "tools":
        if name.startswith("probe_"):
            return "ЗОНД"
        return "ИНСТРУМЕНТ"
    if top == "research":
        if name.startswith("probe_") or name.startswith("analyze_"):
            return "ЗОНД/АНАЛИЗ"
        return "ИССЛЕДОВАНИЕ"
    if top == "PbSdkConsole_Project":
        return "МОСТ (C#)"
    if top == "bridge_src":
        return "МОСТ (копия)"
    if rel.suffix in (".json", ".md", ".bat", ".txt"):
        return "КОНФИГ/ДОК"
    return "ПРОЧЕЕ"


def main():
    t_start = time.time()
    print("[инвентарь] сканирую проект (только чтение)...", flush=True)

    # ---------- фаза 1: обход дерева ----------
    all_paths = [p for p in BASE.rglob("*") if p.is_file() and not skipped(p)]
    bar = Bar(len(all_paths), "обход")
    files = []          # (rel, size, mtime) для отчёта
    agg = defaultdict(lambda: {"n": 0, "size": 0, "oldest": None, "newest": None})
    texts = {}          # rel -> содержимое (для поиска ссылок)
    total_size = 0

    for p in all_paths:
        bar.add()
        rel = p.relative_to(BASE)
        try:
            st = p.stat()
        except OSError:
            continue
        size, mtime = st.st_size, st.st_mtime
        total_size += size
        parts = rel.parts
        top = parts[0] if len(parts) > 1 else ""

        if top in AGG_DIRS:
            mask = data_group(p.name) if top == "data" else output_group(p.name)
            g = agg[(top, mask)]
            g["n"] += 1
            g["size"] += size
            g["oldest"] = mtime if g["oldest"] is None else min(g["oldest"], mtime)
            g["newest"] = mtime if g["newest"] is None else max(g["newest"], mtime)
            # документацию/конфиги из этих папок не читаем — они не код
            continue

        files.append((rel, size, mtime))
        if p.suffix in TEXT_EXT and size <= 2_000_000:
            try:
                texts[rel] = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    bar.done()

    # ---------- фаза 2: поиск ссылок ----------
    # Для .py ищем имя модуля (stem) — так работают импорты и subprocess.
    # Для остальных — имя файла целиком.
    targets = []
    for rel, size, mtime in files:
        if rel.suffix in CODE_EXT or rel.suffix in (".bat", ".json", ".md"):
            needle = rel.stem if rel.suffix == ".py" else rel.name
            if len(needle) >= 4:
                targets.append((rel, needle))

    bar = Bar(len(targets), "ссылки")
    refs = defaultdict(list)
    bat_hits = set()
    bat_names = [r for r in texts if r.suffix == ".bat"]

    for rel, needle in targets:
        bar.add()
        low = needle.lower()
        for other, content in texts.items():
            if other == rel:
                continue
            if low in content.lower():
                refs[rel].append(other.as_posix())
                if other.suffix == ".bat":
                    bat_hits.add(rel)
    bar.done()

    # ---------- фаза 3: классификация ----------
    now_ts = time.time()
    rows = []
    for rel, size, mtime in files:
        age = (now_ts - mtime) / 86400.0
        rlist = sorted(refs.get(rel, []))
        in_bat = rel in bat_hits
        cat = categorize(rel, len(rlist), in_bat)
        content = texts.get(rel, "")
        quik = any(m in content for m in QUIK_MARKERS) if content else False
        rows.append({
            "rel": rel.as_posix(), "size": size, "age": age, "cat": cat,
            "refs": rlist, "bat": in_bat, "quik": quik,
        })
    rows.sort(key=lambda r: (r["cat"], r["rel"]))

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)

    zero = [r for r in rows
            if not r["refs"] and r["cat"] not in ("ТОЧКА ВХОДА", "КОНФИГ/ДОК", "ТЕСТ")
            and Path(r["rel"]).suffix in CODE_EXT]
    zero.sort(key=lambda r: -r["size"])

    quik_rows = [r for r in rows if r["quik"] and Path(r["rel"]).suffix in CODE_EXT]
    biggest = sorted(rows, key=lambda r: -r["size"])[:15]

    # ---------- отчёт ----------
    L = []
    L.append("=" * 100)
    L.append(f" ИНВЕНТАРИЗАЦИЯ ПРОЕКТА — {datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')} МСК")
    L.append(" Только чтение. Ничего не удалено и не изменено.")
    L.append("=" * 100)
    L.append("")
    L.append(f"Всего файлов (без кэша/сборок): {len(all_paths)}, суммарно {human(total_size)}")
    L.append(f"Файлов кода/конфигов в отчёте: {len(rows)}")
    L.append("")
    L.append("-" * 100)
    L.append(" ПО КАТЕГОРИЯМ")
    L.append("-" * 100)
    for cat in sorted(by_cat):
        grp = by_cat[cat]
        L.append(f"  {cat:24s} {len(grp):4d} файлов  {human(sum(g['size'] for g in grp))}")
    L.append("")
    L.append("-" * 100)
    L.append(" ПОЛНЫЙ СПИСОК (путь | размер | возраст | батник | QUIK-след | кто ссылается)")
    L.append("-" * 100)
    for cat in sorted(by_cat):
        L.append("")
        L.append(f"### {cat}")
        for r in by_cat[cat]:
            flag_bat = "БАТ" if r["bat"] else "   "
            flag_q = "QUIK" if r["quik"] else "    "
            L.append(f"  {r['rel']:<58s} {human(r['size']):>9s} "
                     f"{r['age']:6.1f}д {flag_bat} {flag_q}")
            if r["refs"]:
                shown = ", ".join(r["refs"][:6])
                more = f" (+{len(r['refs']) - 6})" if len(r["refs"]) > 6 else ""
                L.append(f"      ссылок {len(r['refs'])}: {shown}{more}")
            else:
                L.append("      ссылок 0: НИКТО не упоминает")
    L.append("")
    L.append("-" * 100)
    L.append(" КАНДИДАТЫ НА РЕВИЗИЮ (код, 0 ссылок, не точка входа)")
    L.append("-" * 100)
    if not zero:
        L.append("  нет")
    for r in zero:
        L.append(f"  {r['rel']:<58s} {human(r['size']):>9s} {r['age']:6.1f}д  {r['cat']}")
    L.append("")
    L.append("-" * 100)
    L.append(" QUIK-СЛЕД (код ссылается на удалённый QUIK — проверить актуальность)")
    L.append("-" * 100)
    if not quik_rows:
        L.append("  нет")
    for r in quik_rows:
        L.append(f"  {r['rel']:<58s} {human(r['size']):>9s} {r['age']:6.1f}д")
    L.append("")
    L.append("-" * 100)
    L.append(" ДАННЫЕ И ОТЧЁТЫ (агрегировано по маскам)")
    L.append("-" * 100)
    for top in ("data", "output"):
        keys = [k for k in agg if k[0] == top]
        if not keys:
            continue
        tot_n = sum(agg[k]["n"] for k in keys)
        tot_s = sum(agg[k]["size"] for k in keys)
        L.append("")
        L.append(f"### {top}/  —  {tot_n} файлов, {human(tot_s)}")
        for k in sorted(keys, key=lambda k: -agg[k]["size"]):
            g = agg[k]
            old = datetime.fromtimestamp(g["oldest"], MSK).strftime("%Y-%m-%d")
            new = datetime.fromtimestamp(g["newest"], MSK).strftime("%Y-%m-%d")
            L.append(f"  {k[1]:<46s} {g['n']:5d} шт {human(g['size']):>10s}  {old} … {new}")
    L.append("")
    L.append("-" * 100)
    L.append(" ТОП-15 САМЫХ ТЯЖЁЛЫХ (код/конфиги)")
    L.append("-" * 100)
    for r in biggest:
        L.append(f"  {r['rel']:<58s} {human(r['size']):>9s}  {r['cat']}")
    L.append("")
    L.append("=" * 100)
    L.append(" РЕШЕНИЕ ПО УДАЛЕНИЮ/ПЕРЕПИСЫВАНИЮ ПРИНИМАЕТ ВЛАДЕЛЕЦ.")
    L.append(" Удаление — по одному файлу за шаг, с проверкой запуска после каждого.")
    L.append("=" * 100)

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(MSK).strftime("%Y-%m-%d_%H-%M-%S")
    rep = OUT_DIR / f"inventory_{stamp}.txt"
    rep.write_text("\n".join(L), encoding="utf-8")

    # ---------- краткая сводка в консоль ----------
    print()
    print("=" * 78)
    print(f" ИНВЕНТАРИЗАЦИЯ — {datetime.now(MSK).strftime('%Y-%m-%d %H:%M')} МСК")
    print("=" * 78)
    print(f"файлов всего: {len(all_paths)}  размер: {human(total_size)}")
    print(f"код/конфиги в отчёте: {len(rows)}")
    print()
    print("ПО КАТЕГОРИЯМ:")
    for cat in sorted(by_cat):
        grp = by_cat[cat]
        print(f"  {cat:24s} {len(grp):4d}  {human(sum(g['size'] for g in grp))}")
    print()
    print(f"КАНДИДАТЫ НА РЕВИЗИЮ (0 ссылок): {len(zero)}")
    for r in zero[:25]:
        print(f"  {r['rel']:<56s} {human(r['size']):>9s} {r['age']:6.1f}д")
    if len(zero) > 25:
        print(f"  ... ещё {len(zero) - 25} (полный список в отчёте)")
    print()
    print(f"QUIK-СЛЕД: {len(quik_rows)} файлов (список в отчёте)")
    print()
    for top in ("data", "output"):
        keys = [k for k in agg if k[0] == top]
        if keys:
            tot_n = sum(agg[k]["n"] for k in keys)
            tot_s = sum(agg[k]["size"] for k in keys)
            print(f"{top}/: {tot_n} файлов, {human(tot_s)}")
            for k in sorted(keys, key=lambda k: -agg[k]["size"])[:8]:
                g = agg[k]
                print(f"  {k[1]:<46s} {g['n']:5d} шт {human(g['size']):>10s}")
    print()
    print(f"[OK] полный отчёт: {rep}")
    print(f"[OK]耗时 {time.time() - t_start:.1f}с".replace("耗时", "время"))
    print("Ничего не удалено. Решение по удалению — за владельцем.")


if __name__ == "__main__":
    main()