# research/ — оглавление (2026-11-24)

Карта скриптов по назначению. Физическая реорганизация в подкаталоги —
на выходных (нужна правка путей в самих скриптах: parents[1] и вызовы
subprocess в tools/morning_report.py).

## Утренний ритуал (вызываются из tools/morning_report.py — НЕ ПЕРЕИМЕНОВЫВАТЬ)
- day_audit.py — аудит дня: TP/FP/FN против эталонов, классы FN (v7).
- replay_alor.py — потоковый реплей ленты Алора (память <50 МБ).
- aggregate_robots.py — склейка эпизодов interval_robot в сущности (Precision 89%).
- reverse_engineer.py — дата-дривен реверс-инжиниринг эталона (v2).
- tw_compare.py — TP/FP/FN против T-Widgets (TIME_TOL_MS=120000).
- tw_fn_summary.py — корзины FN + кандидаты на понижение min_qty.

## Сбор данных
- save_trades.py, save_trades_range.py, save_today.py — сохранение лент.
- split_logs.py — нарезка логов.
- alor_stream_test.py, alor_accum_test.py — зонды стрима Алора.

## Сравнение с эталонами
- ab_compare.py — A/B с конкурентом (TP/FN/FP; эталон quik_trades.csv).
- compare_aniscan_grid.py / compare_aniscan_ours.py / compare_aniscan_tw.py — против aniscan.
- compare_sides.py — сверка сторон сделок (наша лента vs Алор).
- compare_historical_flags.py, compare_flags_parity.py, compare_tp_baseline_noisy.py — паритет флагов и бейзлайны.

## Реплеи и прогоны детектора
- replay_grid.py, replay_periodicity.py, replay_etalon_tape.py.
- run_detectors.py, run_all_dates.py — прогоны по тикерам/датам.
- run_ab_matrix.py — матрица A/B по порогам.

## Анализ, тюнинг, аудиты
- entry_backtest.py — бэктест входов (JSON или Quik-лента).
- market_structure.py — структура рынка.
- analyze_parallel.py, analyze_datasource_probe.py — анализ зондов.
- fp_audit.py, check_fn_series.py — разбор FP/FN.
- diff_tp_from_matrix_log.py — разбор лога матрицы.
- validate_aggregator.py — валидация агрегатора.
- weekly_review.py, week_signals_review.py — недельные обзоры.

## Аудит стакана (офлайн-семья ai_solution_v1)
- orderbook_audit.py, orderbook_selftest.py.

## Расширения (не python)
- t-widgets/ — Chrome-расширение перехватчика TW + userscript.
- aniscan/extension/ — расширение Aniscan.

## Правило при работе с этой папкой
Перед переносом/удалением любого скрипта — проверка ссылок:
git grep по имени файла + просмотр tools/morning_report.py.
