# AGENTS.md — правила для ИИ-соавторов проекта "Приблуда на python"

Этот файл — инструкция для любого ИИ, работающего над проектом.
Попадает в каждый дамп (tools/make_dump.py). Читается ПЕРВЫМ.

## Роль
Ты — второй разработчик. Владелец — трейдер-скальпер без опыта
программирования: он копирует и вставляет код как есть, не редактируя
руками. Код должен быть рабочим СРАЗУ, без "доработай сам",
без пропущенных импортов.

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
- Источник сделок — QUIK: lua-скрипт connectors/quik/export_trades.lua
  пишет data/quik_trades.csv (OnAllTrade, буфер 200 мс, время сделки с мс).
- Источник котировок — MOEX ISS: tools/iss_quotes_sync.py пишет
  data/quik_quotes.csv (раз в 5 c).
- Источник планок — QUIK (медленный опрос getParamEx ломтиками по 10
  тикеров): lua пишет data/quik_limits.csv.
- Legacy (не основной путь): Alor WebSocket, Tkinter leader_monitor.py,
  Telegram-бот, start_all.py (не существует).
- Тесты — pytest, только для detectors/.
Предложения FastAPI/SQLAlchemy/Celery/Redis/Docker проекту НЕ подходят.

## Запуск (три процесса)
1. QUIK: "Расширения → Доступные скрипты" — запущен export_trades.lua;
   открыта таблица "Текущие торги" (класс "МБ ФР: Т+ Акции и ДП" —
   "Добавить все" И инструменты, И параметры; таблицу не закрывать).
2. Консоль 1: python tools/iss_quotes_sync.py (котировки из ISS).
3. Консоль 2: python main.py --source quik (или Приблуда_Quik.bat).

## Проверенные особенности Quik/ISS (2026-08-20, зонды probe_params.lua)
- getParamEx отдаёт значения одиночными вызовами, но НЕ выдерживает
  массовый опрос (506 тикеров за цикл) — поэтому планки собираются
  ломтиками по 10 тикеров за 2 c (полный обход ~2 мин).
- ISS отдаёт котировки акций, но НЕ отдаёт PRICEMAX/PRICEMIN по акциям —
  планки только из Quik.
- В Quik CLOSE не отдаётся до первой сделки дня — используется
  PREVWAPRICE; TRADINGSTATUS приходит с param_type=4 (get_num принимает 1 и 4).
- Коды TRADINGSTATUS у ISS другие — в quik_quotes.csv не пишутся;
  статусы аукционов вкладка "Аукционы" берёт по расписанию MOEX.
- Класс SPBFUT в терминале не подключён — фьючерсов в котировках нет
  (не критично: фандинг берётся с ISS fort).

## Структура
detectors/ — детектор роботов (interval_robot), базовые классы
core/ — config, ticker_settings, auction_settings, sound_manager и др.
connectors/quik/ — CSV-бэкенд, ридеры limits/quotes, lua-экспортёр, зонд
gui/ — главное окно (PySide6), мини-окна, диалоги;
       gui/tabs/ — вкладки: limits/, auctions/, arbitrage/, funding/, charts/
modules/arbitrage/ — PairMonitor, pairs_config, live_spread
modules/leader_monitor.py — legacy (Tkinter)
integrations/ — funding_iss.py, tg_bot/ (legacy)
tools/ — make_dump, cleanup, iss_quotes_sync, диагностики, пробники
research/ — офлайн-исследования (replay_quik_csv, quik_csv_diag, бэктесты)
tests/ — тесты детектора (10/10 на 2026-08-20)

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

## Формат ответов
- Изменённые файлы — только целиком.
- Чётко, без воды.
- Не выдумывать: API/формат не проверен — скажи прямо и предложи зонд
  (см. connectors/quik/probe_params.lua, tools/probe_*).

## Инструменты
- python -m pytest tests/ -v — тесты детектора.
- python tools/make_dump.py — дамп для помощника (включает .lua).
- python tools/cleanup.py [--do] — очистка output/data/__pycache__.
- python research/replay_quik_csv.py — реплей ленты через боевые детекторы.
- python tools/quik_csv_diag.py — диагностика CSV Quik (стороны, время, пробелы).
- python tools/iss_quotes_sync.py — котировки ISS → data/quik_quotes.csv.