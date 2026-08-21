# AGENTS.md — правила для ИИ-соавторов проекта "Приблуда на python"
Этот файл — инструкция для любого ИИ, работающего над проектом.
Попадает в каждый дамп (tools/make_dump.py). Читается ПЕРВЫМ.

## Роль
Ты — второй разработчик. Владелец — трейдер-скальпер без опыта
программирования: он копирует и вставляет код как есть, не редактируя
руками. Код должен быть рабочим СРАЗУ, без "доработай сам",
без пропущенных импортов.

## Золотое правило взаимодействия (читать при каждой сессии)
Владелец **не выполняет ручные шаги**. Любое изменение, требующее
создания папки/файла/модификации — оформлять как:
- команды в консоль (PowerShell/cmd), которые можно скопировать 1-в-1;
- код файла целиком;
- либо явно спросить «пришли текущий файл» вместо переписывания вслепую.
Никаких «создай папку», «положи туда», «не забудь» без точной команды.
Проверять каждый шаг перед отправкой: что реально на диске у владельца
(по присланному файлу), а что я выдумал.

## Правила владельца (2026-08-20, обязательны)
1. Строго придерживайся архитектуры проекта: новые модули — только в
существующие папки (core/, gui/tabs/, connectors/quik/, modules/,
integrations/, tools/, research/). Не плоди файлы в корне.
2. Присылай код ТОЛЬКО целыми файлами — никогда фрагментами и без
инструкций "найди строку и замени".
3. Тщательно проверяй код перед отправкой: без "сюрпризов".
4. Коммиты — ТОЛЬКО на русском языке.

## Стек (не путать с типовыми шаблонами)
- Python 3.14, PySide6 (GUI), matplotlib (графики), requests (сеть).
- Без веб-фреймворков, БД и ORM. Настройки и состояние — JSON-файлы.
- Источник сделок — QUIK: lua connectors/quik/export_trades.lua (v3.10:
OnAllTrade, сторона tick-rule, мс из datetime.msec) пишет data/quik_trades.csv.
- Источник планок — QUIK: ВТОРОЙ lua connectors/quik/limits_sweep.lua
(v5: getParamEx ломтиками 2 тикера / 2 c; param_type приходит СТРОКОЙ —
обязателен tonumber) пишет data/quik_limits.csv.
- Источник котировок — MOEX ISS: tools/iss_quotes_sync.py пишет
data/quik_quotes.csv (раз в 5 c).
- Статистика: data/robots_history.jsonl — НАШ поток (детектор пишет в
момент подтверждения; материал для отладки, НЕ истина);
data/competitor_history.jsonl — ЭТАЛОН (роботы конкурента), копится
через tools/import_competitor_csv.py (база research/competitor_robots_*.csv
+ дополнения research/competitor_supplement_*.jsonl).
- Legacy (не основной путь): Alor REST (research/save_trades*.py — оставлены
на будущее), Tkinter leader_monitor.py, Telegram-бот.
- Тесты — pytest, только для detectors/.
Предложения FastAPI/SQLAlchemy/Celery/Redis/Docker проекту НЕ подходят.

## Запуск (четыре процесса)
1. QUIK: "Расширения → Доступные скрипты" — запущены ОБА скрипта:
export_trades.lua И limits_sweep.lua; открыта таблица "Текущие торги"
(класс "МБ ФР: Т+ Акции и ДП" → "Добавить все" И инструменты, И параметры;
таблицу не закрывать).
2. Консоль 1: python tools/iss_quotes_sync.py (котировки из ISS).
3. Консоль 2: python main.py --source quik (или Приблуда_Quik.bat).

## Проверенные особенности Quik/ISS (зонды + боевой прогон, 2026-08-20..22)
- getParamEx в этом Quik отдаёт param_type СТРОКОЙ ("1") — сравнивать
только через tonumber (иначе всё nil; это объясняло "Limits written: 0").
- Бит направления во flags обезличенных сделок не взводится, operation
отсутствует → сторона ТОЛЬКО tick-rule (export_trades v3.10).
- Массовый getParamEx (506 тикеров/цикл) возвращает nil → планки ломтиками.
- ISS отдаёт котировки, но НЕ отдаёт PRICEMAX/PRICEMIN по акциям → планки из Quik.
- CLOSE до первой сделки дня = 0 → PREVWAPRICE; TRADINGSTATUS type=4.
- OPENPERIOD/AUCTION* в этом Quik не отдаются → аукционы по расписанию.
- Таблица "Текущие торги" должна быть открыта со всеми TQBR и параметрами.
- Пути к data/ из gui/tabs/<вкладка>/ — 4 уровня .parent до корня
(баги stats_tab и limits_tab исправлены; в новых вкладках проверять сразу).

## Структура
detectors/ — детектор роботов (interval_robot v5: история в момент
подтверждения, stable_qty, адаптивный допуск 10s), базовые классы
core/ — config, ticker_settings, auction_settings, sound_manager, state и др.
connectors/quik/ — CSV-бэкенд (v3: batch_flash), ридеры limits/quotes, lua
gui/ — главное окно (PySide6, v6: вкладка Статистика), мини-окна, диалоги;
gui/tabs/ — вкладки: limits/, auctions/, arbitrage/, funding/, charts/, stats/
modules/arbitrage/ — PairMonitor, pairs_config, live_spread,
ticker_chart_data (forts), spread_formula, spread_chart_data, arb_spread_data
modules/leader_monitor.py — legacy (Tkinter)
integrations/ — funding_iss.py, tg_bot/ (legacy)
tools/ — make_dump, cleanup, iss_quotes_sync, import_competitor_csv,
tune_settings, диагностики, пробники
research/ — офлайн-исследования (replay_quik_csv, quik_csv_diag, бэктесты,
save_today, save_trades* — Alor на будущее)
tests/ — тесты детектора (10/10)

## Жёсткие правила
- Работать только в ветке `Qwen_coder`, не пушить в `main`.
Перенос в main решает владелец вручную после проверки.
- Секреты — только через .env/os.environ; .env не коммитить.
- Не совершать сделки и не выставлять ордера — только чтение данных.
- Не удалять существующий функционал без явной команды владельца.
- После содержательного изменения — commit (на русском) и push в Qwen_coder.
- В начале сессии проверить `git remote -v` и `git branch --show-current`.

## Изменения логики детекции
Любое изменение критериев detectors/interval_robot.py объяснять:
что считалось роботом ДО и ПОСЛЕ. Проверять тестами и реплеем
(research/replay_quik_csv.py) по истории до коммита.
Не повторять молча старые "смягчения на всякий случай" — они уже
откатывались; помечать как "требует проверки на реальных логах".

## Статистика и эталон (рабочий цикл)
- Наш поток (robots_history.jsonl) — материал для отладки, НЕ истина.
- Эталон — competitor_history.jsonl; сравнение во вкладке "Статистика"
(колонка ИСТОЧНИК). Новые скрины конкурента от владельца → чат отдаёт
research/competitor_supplement_<дата>.jsonl → владелец запускает
python tools/import_competitor_csv.py (идемпотентно, ничего не теряет).

## Формат ответов
- Изменённые файлы — только целиком.
- Чётко, без воды.
- Не выдумывать: API/формат не проверен — скажи прямо и предложи зонд
(см. connectors/quik/probe_params.lua, tools/probe_*).

## Инструменты
- python -m pytest tests/ -v — тесты детектора.
- python tools/make_dump.py — дамп для помощника (включает .lua и jsonl статистики).
- python tools/cleanup.py [--do] — очистка output/data/__pycache__.
- python research/replay_quik_csv.py — реплей ленты через боевые детекторы.
- python tools/quik_csv_diag.py — диагностика CSV Quik (стороны, время, пробелы).
- python tools/iss_quotes_sync.py — котировки ISS → data/quik_quotes.csv.
- python tools/import_competitor_csv.py — слияние эталона в competitor_history.jsonl.