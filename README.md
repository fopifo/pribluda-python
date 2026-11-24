# Приблуда на python

Скринер торговых роботов на Московской бирже (MOEX) для трейдера-скальпера.
Ищет серии сделок с устойчивым интервалом (признак алгоритма), следит за
арбитражными связками, планками, аукционами, фандингом; живые графики;
статистика "наш детектор vs эталон (конкурент + aniscan)".

## Актуальная архитектура (2026-11-24)

Источники данных:
- **Мост CScalp/PbSdk** — основной live-источник ленты и стакана.
  Код коннектора: `connectors/bridge/` (переименован из `connectors/quik`,
  QUIK не используется с 2026-11-08). Пишет в `data/pbsdk_trades_*.jsonl`
  и `data/orderbook_*.jsonl`. Мост работает с 2026-11-15 — ранее этой
  даты отсутствие лент НОРМА.
- **Алор WebSocket** — резерв live + дозагрузка завершённых сессий
  (`tools/alor_download_day.py`, `research/replay_alor.py`).
- **T-Widgets (Mind Stocks)** — эталон №2, Chrome-расширение перехватывает
  SignalS-кадры и POST-ит на `tools/tw_server.py` (порт 8765).
- **Aniscan.ru** — эталон №1 (история роботов, `tools/aniscan_robot_history.py`).

## Быстрый старт (ежедневный запуск)
1. `pip install -r requirements.txt`
2. Запуск `start_all.bat` — поднимает 6 служб:
   TW Server → Chrome + T-Widgets → PbSdk Bridge → Snapshot Positions →
   Arbitrage Main (`python main.py --source bridge`) → MAX bot.
3. В терминале Т-Банка войти в терминал — расширение начнёт слать роботов.

Алиас `--source quik` сохранён: работает так же, как `bridge`.

## Вкладки GUI
- Роботы: 4 блока (buy/sell x подтверждённые/2-3 повтора), колонки
  CD/NEXT/MS/LPP, анти-шум v6, чипы планок, batch_flash.
- Планки: дистанция до планки, зона %, звук/мигание (limits_ui.json).
- Аукционы: эшелоны, расписание MOEX, статусы, группы.
- Арбитраж: связки из arb_pairs.json, спред, EMA-база, "ПРОСТРЕЛ".
- Фандинг: ставки свопа фьючерсов (ISS).
- Графики: живая цена тикера или спред двух (T1 - T2).
- Статистика: агрегат истории роботов (наш/конкурент/aniscan).

Мини-окна: кнопка "Мини" — два оверлея до 10 ячеек (mini_window_config.json).

## Детекторы (две семьи — сознательно)
1. **Live-семья** (`detectors/`, `modules/`):
   - `interval_robot.py` (v13.4) — основной live-детектор: период = медиана
     зазоров, k-кратные, max_qty_variants=4, regularity-гейт 0.6.
   - `stream_grid.py` (v2.4) — стриминг-детектор сеток, интегрирован в
     backend, вкл/выкл через USE_STREAM_GRID в core/config.py.
   - `periodicity_detector.py`, `sweep_detector.py`, volume_spike,
     level_break, spring_monitor.
2. **Офлайн-семья** (`ai_solution_v1/`) — новая ветка, калибруется на
   исторических данных:
   - `detect/interval.py` — IntervalDetector (новый детектор ленты).
   - `orderbook.py` — OrderbookDetector (события стакана, спуфы).
   - `combined.py` — связыватель лента+стакан -> Мега-Сигналы ->
     result.txt + буфер обмена. НЕ участвует в live-режиме.
   - `pipeline_validate.py` — валидация на jsonl с фильтрами шума.
   - `detect/registry.py` — реестр детекторов по тикерам.

Пороги по тикерам: `ticker_settings.json`
(active, min_qty, min_interval, max_interval, interval_tolerance,
max_qty_ratio, min_display_repeats, min_double_hit_gap_sec).

## Данные (data/)
- pbsdk_trades_YYYY-MM-DD.jsonl — лента сделок (мост, основной).
- orderbook_YYYY-MM-DD.jsonl — стакан (мост).
- tw_robots_YYYY-MM-DD.jsonl — эталон T-Widgets (расширение).
- robots_history.jsonl — наш детектор (в момент подтверждения).
- competitor_history.jsonl — эталон конкурента.
- aniscan_history.jsonl — эталон aniscan (APPEND + дедуп).
- <TICKER>_<ДАТА>.json — лента Алора (исследования).

## Эталон (рабочий цикл)
- Конкурент: скрины -> tools/import_competitor_csv.py -> competitor_history.
- Aniscan: `tools/aniscan_download_day.py <ДАТА>` (cookies в .env).
- T-Widgets: расширение в Chrome + tw_server.py (автоматически).

## Утренний ритуал
`python tools/morning_report.py` — фазы: инвентарь -> покрытие мостом ->
replay_alor -> day_audit -> сводка (output/morning_report_*.txt).
Планируется добавление clean_tw_robots -> tw_compare -> tw_fn_summary.

## Исследования (research/)
Скрипты A/B-сравнений, реплеев и аудитов. Полный инвентарь:
`python tools/inventory.py`. Ключевые: ab_compare, replay_alor,
replay_grid, compare_aniscan_*, day_audit, week_signals_review.

## Тесты
`python -m pytest tests/ -v`

## Помощнику
- Правила ИИ — в AGENTS.md и PROJECT_NOTES.md (правило надмножества!).
- Дамп проекта: `python tools/make_dump.py` -> project_dump.txt.
- Коммиты — на русском. Ветка: main (repo pribluda-python).
