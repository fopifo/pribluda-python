"""
ai_solution_v1/cli.py
=====================
Командная строка для офлайн-прогона ленты через детектор роботов.
Поддерживает также анализ стакана (--orderbook).

Примеры:
    python -m ai_solution_v1.cli --file data/pbsdk_trades_2026-11-19.jsonl
    python -m ai_solution_v1.cli --file data/pbsdk_trades_2026-11-19.jsonl --symbol SBER
    python -m ai_solution_v1.cli --file data/pbsdk_trades_2026-11-19.jsonl --symbol VTBR GAZP --min-qty 10
    python -m ai_solution_v1.cli --file tape.jsonl --limit 100000 --json
    python -m ai_solution_v1.cli --orderbook data/orderbook_2026-11-18.jsonl
    python -m ai_solution_v1.cli --orderbook data/orderbook_2026-11-18.jsonl --events-out spoof.jsonl

Только чтение данных. Нет сети, нет GUI, нет отправки ордеров и нет
подключения к брокеру — модуль физически не содержит таких вызовов.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ai_solution_v1.config import DEFAULT_CONFIG, SettingsError
from ai_solution_v1.pipeline import PipelineConfig, format_summary, run_file
from ai_solution_v1.orderbook import (
    OrderbookConfig,
    format_orderbook_report,
    run_orderbook,
)

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai_solution_v1.cli",
        description="Офлайн-детектор роботов Мосбиржи: прогон JSONL-ленты "
                    "(CScalp / мост) без GUI и без торговли.",
    )
    p.add_argument(
        "--file", default=None, metavar="PATH",
        help="путь к JSONL-ленте (построчно: symbol, side, qty, price, timestamp). "
             "Обязателен для анализа ленты; не нужен с --orderbook",
    )
    p.add_argument(
        "--orderbook", default=None, metavar="PATH", dest="orderbook",
        help="путь к JSONL-файлу стакана (orderbook). Запускает анализ стакана "
             "вместо анализа ленты.",
    )
    p.add_argument(
        "--symbol", nargs="+", default=None, metavar="TICKER",
        help="тикер(ы) для анализа; по умолчанию — все тикеры в файле",
    )
    p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="прочитать только первые N строк (0 = весь файл)",
    )
    # --min-qty и все прочие пороги детектора НЕ добавляются вручную:
    # они генерируются ниже из полей DetectorConfig (min_qty входит в их
    # число). Ручное добавление давало конфликт
    # "argument --min-qty: conflicting option string".

    det = p.add_argument_group("параметры детектора (переопределяют config.py)")
    existing = {opt for act in p._actions for opt in act.option_strings}
    for name in DEFAULT_CONFIG.field_names():
        if name in ("symbol", "preset_name"):
            continue
        flag = f"--{name.replace('_', '-')}"
        if flag in existing:
            # Защита от будущих дублей: не роняем parser.
            continue
        cur = getattr(DEFAULT_CONFIG, name)
        if isinstance(cur, bool):
            det.add_argument(
                flag, dest=name, action="store_true",
                default=None, help=f"{name} (по умолчанию: {cur})",
            )
        elif isinstance(cur, int):
            det.add_argument(
                flag, dest=name, type=int,
                default=None, metavar="INT", help=f"{name} (по умолчанию: {cur})",
            )
        elif isinstance(cur, float):
            det.add_argument(
                flag, dest=name, type=float,
                default=None, metavar="FLOAT", help=f"{name} (по умолчанию: {cur})",
            )
        else:
            det.add_argument(
                flag, dest=name, default=None,
                metavar="VAL", help=f"{name} (по умолчанию: {cur})",
            )

    out = p.add_argument_group("вывод")
    out.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    out.add_argument(
        "--events-out", default=None, metavar="PATH",
        help="сохранить найденные события (spoof/wall или сигналы) в JSONL-файл",
    )
    out.add_argument(
        "--no-progress", action="store_true", help="не рисовать прогресс-бар",
    )
    out.add_argument(
        "--top-n", type=int, default=5, metavar="N",
        help="сколько групп вывести в отчёт (по умолчанию 5)",
    )
    out.add_argument(
        "--progress-every", type=int, default=100_000, metavar="N",
        help="строк между перерисовками прогресс-бара (по умолчанию 100000)",
    )
    out.add_argument(
        "--progress-interval", type=float, default=1.0, metavar="SEC",
        help="минимальный интервал между перерисовками по времени, сек (1.0)",
    )

    cmp_grp = p.add_argument_group("сравнение с эталоном Aniscan")
    cmp_grp.add_argument(
        "--compare-aniscan", default=None, metavar="PATH", dest="compare_aniscan",
        help="эталон aniscan_robots_*.jsonl: TP/FP/FN, Precision/Recall и "
             "топ-10 по эталону и по нашему детектору",
    )
    cmp_grp.add_argument(
        "--interval-tol", type=float, default=0.20, metavar="FLOAT",
        help="допуск по интервалу при матчинге, доля (0.20 = плюс-минус 20%%)",
    )
    cmp_grp.add_argument(
        "--window-sec", type=int, default=300, metavar="SEC",
        help="окно сопоставления по времени (по умолчанию 300 = 5 минут)",
    )
    cmp_grp.add_argument(
        "--min-hits", type=int, default=0, metavar="N",
        help="учитывать только наши серии с >= N ударами (0 = все)",
    )
    cmp_grp.add_argument(
        "--only-reported", action="store_true",
        help="учитывать только серии, прошедшие порог качества детектора",
    )

    ob_grp = p.add_argument_group("параметры стакана")
    ob_grp.add_argument(
        "--spoof-ttl-sec", type=float, default=5.0, metavar="SEC", dest="spoof_ttl_sec",
        help="максимальное время жизни 'мигалки' (по умолчанию 5.0 сек)",
    )
    ob_grp.add_argument(
        "--spoof-qty-factor", type=float, default=5.0, metavar="FLOAT", dest="spoof_qty_factor",
        help="во сколько раз объем должен превышать EMA (по умолчанию 5.0)",
    )
    ob_grp.add_argument(
        "--spoof-min-qty", type=int, default=100, metavar="INT", dest="spoof_min_qty",
        help="минимальный объем для 'мигалки' (по умолчанию 100)",
    )
    ob_grp.add_argument(
        "--wall-hold-sec", type=float, default=30.0, metavar="SEC", dest="wall_hold_sec",
        help="время, после которого заявка считается 'стеной' (по умолчанию 30.0 сек)",
    )
    ob_grp.add_argument(
        "--wall-min-qty", type=int, default=100, metavar="INT", dest="wall_min_qty",
        help="минимальный объем для 'стены' (по умолчанию 100)",
    )

    return p


def _overrides_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Собрать overrides из флагов детектора (только заданные явно)."""
    ov: Dict[str, Any] = {}
    for name in DEFAULT_CONFIG.field_names():
        if name in ("symbol", "preset_name"):
            continue
        val = getattr(args, name, None)
        if val is None:
            continue
        ov[name] = val
    return ov


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

    # --- ВЕТКА АНАЛИЗА СТАКАНА ---
    if args.orderbook:
        path = Path(args.orderbook)
        if not path.exists():
            print(f"Ошибка: файл стакана не найден: {path}", file=sys.stderr)
            return 2
        if path.is_dir():
            print(f"Ошибка: ожидался файл стакана, а не папка: {path}", file=sys.stderr)
            return 2

        ob_config = OrderbookConfig(
            spoof_ttl_ms=int(args.spoof_ttl_sec * 1000),
            spoof_qty_factor=args.spoof_qty_factor,
            spoof_min_qty=args.spoof_min_qty,
            wall_hold_ms=int(args.wall_hold_sec * 1000),
            wall_min_qty=args.wall_min_qty,
        )

        event_writer = None
        if args.events_out:
            try:
                event_writer = open(args.events_out, "w", encoding="utf-8")
            except IOError as e:
                print(f"Ошибка при открытии файла для записи событий: {e}", file=sys.stderr)
                return 2

        def handle_event(event: Any) -> None:
            if event_writer:
                event_writer.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

        symbols = tuple(sorted({s.strip().upper() for s in args.symbol})) if args.symbol else None
        try:
            report = run_orderbook(
                path,
                config=ob_config,
                symbols=symbols,
                limit_lines=max(0, args.limit),
                on_event=handle_event,
            )
        finally:
            if event_writer:
                event_writer.close()

        if args.json:
            json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print(format_orderbook_report(report))
        return 0

    # --- ВЕТКА АНАЛИЗА ЛЕНТЫ (стандартный путь) ---
    if not args.file:
        print("Ошибка: не указан ни --file (лента), ни --orderbook (стакан).", file=sys.stderr)
        return 2

    path = Path(args.file)
    if not path.exists():
        print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
        return 2
    if path.is_dir():
        print(f"Ошибка: ожидался файл ленты, а не папка: {path}", file=sys.stderr)
        return 2

    symbols = tuple(sorted({s.strip().upper() for s in args.symbol})) if args.symbol else None
    overrides = _overrides_from_args(args)

    cfg = PipelineConfig(
        file=path,
        symbols=symbols,
        global_overrides=overrides,
        limit_lines=max(0, args.limit),
        progress_every=max(1, args.progress_every),
        progress_min_interval=max(0.1, args.progress_interval),
        top_n=max(1, args.top_n),
        progress=not args.no_progress,
    )

    if args.compare_aniscan:
        return _run_comparison(args, path, symbols, overrides)

    try:
        report = run_file(cfg)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except SettingsError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130

    if args.json:
        json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(format_summary(report))
    return 0


def _run_comparison(
    args: argparse.Namespace,
    path: Path,
    symbols: Optional[tuple],
    overrides: Dict[str, Any],
) -> int:
    """Сравнение с эталоном Aniscan (--compare-aniscan)."""
    from ai_solution_v1.metrics import MatchConfig, compare, format_comparison

    ref_path = Path(args.compare_aniscan)
    if not ref_path.exists():
        print(f"Ошибка: эталон не найден: {ref_path}", file=sys.stderr)
        return 2
    try:
        res, report, ref_stats = compare(
            str(path),
            str(ref_path),
            symbols=symbols,
            limit_lines=max(0, args.limit),
            overrides=overrides,
            match_cfg=MatchConfig(
                window_ms=max(1, args.window_sec) * 1000,
                interval_tol=args.interval_tol,
                min_hits=max(0, args.min_hits),
                require_reported=bool(args.only_reported),
            ),
            progress=not args.no_progress,
            top_n=max(1, args.top_n),
        )
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except SettingsError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130

    if args.json:
        payload = {
            "metrics": res.to_dict(),
            "reference": dict(ref_stats),
            "run": report.to_dict(),
            "top_reference": [
                {"ticker": r.ticker, "side": "buy" if r.side == 1 else "sell",
                 "interval_ms": r.interval_ms,
                 "matched": any(pr is r for _, pr in res.tp_pairs)}
                for r in sorted(res.fn_robots + [r for _, r in res.tp_pairs],
                                key=lambda r: r.ticker)[:10]
            ],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(format_comparison(res, report, ref_stats, top_n=max(1, args.top_n)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())