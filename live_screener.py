"""
Приблуда на python — живой скринер: подключается к WebSocket Алора,
получает сделки в реальном времени и прогоняет через те же детекторы,
что и исторический run_detectors.py — но сигналы появляются сразу, без
ожидания конца дня.

Список тикеров, их активность (мониторим/нет) и ручные пороги (мин.
лотов, мин. сек интервала, мин. повторов) берутся из
ticker_settings.json (см. ticker_settings.py) — редактируются через GUI
("⚙ Настройки тикеров").

ЖИВОЕ ПРИМЕНЕНИЕ НАСТРОЕК: settings_watcher_loop раз в
SETTINGS_POLL_INTERVAL_SEC секунд перечитывает ticker_settings.json и
применяет разницу на лету — подробности см. в _apply_settings_diff.

АРБИТРАЖ: тот же поток сделок кормит мониторы арбитражных связок
(arbitrage/pair_monitor.py, связки — в arb_pairs.json). У каждой связки
свой режим:
  - "ratio_pct" — отклонение отношения цен в процентах (нейтральный
    сигнал "расхождение")
  - "absolute_rub" — отклонение абсолютной разницы цен в рублях
    (позитивный сигнал "прострел" — торговая возможность, плюс
    отдельный сигнал "схождение" — когда пора выходить из связки)

info_tabs_loop передаёт now_ts в snapshot() — монитор связки помнит
последнее событие ещё 30 секунд (DISPLAY_HOLD_SEC в pair_monitor.py),
чтобы GUI не пропустил короткоживущий прострел между двумя опросами
(опрос раз в INFO_TABS_INTERVAL_SEC секунд).

ВРЕМЕННО (пока не трогаем tg_bot/): в Telegram уходят только сигналы
режима "ratio_pct" вида "divergence" — существующий bot.py умеет
форматировать только их (проценты). "Прострелы"/"схождения"
(absolute_rub, MTLR/MTLRP) в Telegram пока не уходят — только в лог и
GUI. Как только bot.py будет обновлён под оба режима — снять это
ограничение в _notify_arb.

ФАНДИНГ / НОВОСТИ: отдельные независимые источники (tg_bot/funding.py,
tg_bot/news_moex.py), заводятся своими отдельными экземплярами кэша.

TELEGRAM: РОБОТЫ НЕ отправляются в Telegram (см. _notify_robot) —
сигнал приходит с задержкой, только когда серия закрылась. Наблюдение
за роботами — через GUI.

ЛОГ: пишется в output/live_signals_<дата>.txt, новый файл каждый день.
Хранятся только последние LOG_RETENTION_DAYS дней — старые файлы
удаляются автоматически при запуске (rotate_old_logs).

СЕТЕВАЯ УСТОЙЧИВОСТЬ: get_access_token использует HTTP-сессию с
автоматическими повторными попытками — на некоторых сетях изредка
рвётся TLS-соединение, это разовый сетевой сбой, не проблема запроса.

Помимо этого работает "сторож" (watchdog) — раз в WATCHDOG_INTERVAL_SEC
секунд проверяет все активные серии по всем тикерам и предупреждает,
если робот просрочил ожидаемый следующий удар.

WebSocket и детекторы работают в ОТДЕЛЬНОМ ФОНОВОМ ПОТОКЕ, Tkinter-окно
— в главном потоке (жёсткое требование самого Tkinter).

Access Token живёт 30 минут — соединение переустанавливается заново
каждые RECONNECT_INTERVAL_SEC секунд; состояние детекторов и
арбитражных мониторов при этом СОХРАНЯЕТСЯ.

Нужны библиотеки: pip install websockets aiohttp requests --break-system-packages
"""

import asyncio
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import websockets
from websockets.exceptions import ConnectionClosed
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
TG_BOT_DIR = BASE_DIR / "tg_bot"

sys.path.insert(0, str(TG_BOT_DIR))

from arb_config import load_pairs, get_pair_symbols
from arbitrage.pair_monitor import PairMonitor
from config import get_detector_configs, get_min_qty_percentile
from dashboard.collector import collect_rows
from detectors.interval_robot import IntervalRobotDetector
from funding import FundingCache, funding_refresh_loop, NAMES as FUNDING_NAMES
from gui.state import SharedState
from gui.window import RobotDashboardWindow
from news_moex import NewsCache, news_refresh_loop
from stats import qty_percentile
from ticker_settings import get_active_symbols, load_settings
from tg_bot.bot import telegram_bot

ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
OAUTH_URL = "https://oauth.alor.ru/refresh"
WS_URL = "wss://api.alor.ru/ws"

BOARD = "TQBR"
EXCHANGE = "MOEX"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

RECONNECT_INTERVAL_SEC = 20 * 60
WATCHDOG_INTERVAL_SEC = 1
DASHBOARD_INTERVAL_SEC = 1
SETTINGS_POLL_INTERVAL_SEC = 5
INFO_TABS_INTERVAL_SEC = 5
LOG_RETENTION_DAYS = 14  # старые live_signals_*.txt удаляются автоматически


class LiveState:
    def __init__(
        self,
        settings: dict,
        detectors: dict[str, list[IntervalRobotDetector]],
        arb_monitors: list[PairMonitor],
        arb_symbols: set[str],
    ):
        self.settings = settings
        self.detectors = detectors
        self.arb_monitors = arb_monitors
        self.arb_symbols = arb_symbols
        self.guid_to_symbol: dict[str, str] = {}
        self.symbol_to_guid: dict[str, str] = {}
        self.ws = None
        self.access_token: str | None = None


def _make_resilient_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_access_token(refresh_token: str) -> str:
    session = _make_resilient_session()
    response = session.post(OAUTH_URL, params={"token": refresh_token})
    response.raise_for_status()
    return response.json()["AccessToken"]


def find_latest_file(symbol: str) -> Path | None:
    candidates = sorted(DATA_DIR.glob(f"{symbol}_*.json"))
    return candidates[-1] if candidates else None


def compute_min_qty(symbol: str, override: dict) -> int:
    manual = override.get("min_qty")
    if manual is not None:
        print(f"  {symbol}: min_qty = {manual} (задано вручную в ticker_settings.json)")
        return manual

    data_file = find_latest_file(symbol)
    if data_file is None:
        print(f"  {symbol}: нет сохранённой истории — min_qty=1 (без фильтра)")
        return 1
    with open(data_file, encoding="utf-8") as f:
        trades = json.load(f)
    pct = get_min_qty_percentile(symbol)
    return qty_percentile(trades, pct)


def build_detectors(settings: dict) -> dict[str, list[IntervalRobotDetector]]:
    detectors: dict[str, list[IntervalRobotDetector]] = {}
    for symbol in get_active_symbols(settings):
        override = settings.get(symbol, {})
        min_qty = compute_min_qty(symbol, override)
        configs = get_detector_configs(symbol, min_qty, override)
        detectors[symbol] = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
        print(f"{symbol}: min_qty = {min_qty}")
    return detectors


def build_arb_monitors() -> tuple[list[PairMonitor], set[str]]:
    pairs = load_pairs()
    monitors = []
    for pair_name, cfg in pairs.items():
        mode = cfg.get("mode", "ratio_pct")
        threshold = cfg.get("threshold", cfg.get("threshold_pct", 1.5))
        monitors.append(PairMonitor(
            pair_name=pair_name,
            symbol_a=cfg["symbol_a"],
            symbol_b=cfg["symbol_b"],
            mode=mode,
            threshold=threshold,
            half_life_sec=cfg.get("half_life_sec", 600.0),
        ))
        unit = "₽" if mode == "absolute_rub" else "%"
        print(f"Арбитраж: связка {pair_name} ({cfg['symbol_a']}/{cfg['symbol_b']}), "
              f"режим {mode}, порог {threshold}{unit}")
    return monitors, get_pair_symbols(pairs)


def build_subscription_symbols(live_state: LiveState) -> set[str]:
    return set(live_state.detectors) | live_state.arb_symbols


def rotate_old_logs() -> None:
    """Удаляет live_signals_*.txt старше LOG_RETENTION_DAYS дней —
    вызывается один раз при старте, чтобы папка output/ не росла
    бесконечно за недели/месяцы работы."""
    if not OUTPUT_DIR.exists():
        return
    cutoff = datetime.now(MOSCOW_TZ).date() - timedelta(days=LOG_RETENTION_DAYS)
    for log_path in OUTPUT_DIR.glob("live_signals_*.txt"):
        date_str = log_path.stem.replace("live_signals_", "")
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            log_path.unlink()
            print(f"Удалён старый лог: {log_path.name}")


def open_log_file():
    OUTPUT_DIR.mkdir(exist_ok=True)
    rotate_old_logs()
    today = datetime.now(MOSCOW_TZ).date()
    log_path = OUTPUT_DIR / f"live_signals_{today}.txt"
    return log_path, open(log_path, "a", encoding="utf-8")


def log_line(log_file, line: str) -> None:
    stamp = datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
    full_line = f"[{stamp}] {line}"
    log_file.write(full_line + "\n")
    log_file.flush()


async def _subscribe_symbol(live_state: LiveState, symbol: str) -> None:
    if symbol in live_state.symbol_to_guid:
        return
    guid = str(uuid.uuid4())
    live_state.guid_to_symbol[guid] = symbol
    live_state.symbol_to_guid[symbol] = guid
    subscribe_msg = {
        "opcode": "AllTradesGetAndSubscribe",
        "exchange": EXCHANGE,
        "code": symbol,
        "instrumentGroup": BOARD,
        "depth": 0,
        "includeVirtualTrades": False,
        "format": "Simple",
        "guid": guid,
        "token": live_state.access_token,
    }
    await live_state.ws.send(json.dumps(subscribe_msg))


async def _unsubscribe_symbol(live_state: LiveState, symbol: str) -> None:
    guid = live_state.symbol_to_guid.pop(symbol, None)
    if guid is None:
        return
    live_state.guid_to_symbol.pop(guid, None)
    unsubscribe_msg = {
        "opcode": "unsubscribe",
        "guid": guid,
        "token": live_state.access_token,
    }
    await live_state.ws.send(json.dumps(unsubscribe_msg))


async def _apply_settings_diff(
    live_state: LiveState, old_settings: dict, new_settings: dict, log_file
) -> None:
    old_active = set(get_active_symbols(old_settings))
    new_active = set(get_active_symbols(new_settings))

    for symbol in sorted(new_active - old_active):
        override = new_settings.get(symbol, {})
        min_qty = compute_min_qty(symbol, override)
        configs = get_detector_configs(symbol, min_qty, override)
        live_state.detectors[symbol] = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
        await _subscribe_symbol(live_state, symbol)
        log_line(log_file, f"ВКЛЮЧЁН  {symbol} (min_qty={min_qty})")

    for symbol in sorted(old_active - new_active):
        if symbol in live_state.detectors:
            del live_state.detectors[symbol]
            if symbol not in live_state.arb_symbols:
                await _unsubscribe_symbol(live_state, symbol)
            log_line(log_file, f"ОТКЛЮЧЁН  {symbol}")

    for symbol in sorted(old_active & new_active):
        if new_settings.get(symbol) == old_settings.get(symbol):
            continue
        override = new_settings.get(symbol, {})
        min_qty = compute_min_qty(symbol, override)
        configs = get_detector_configs(symbol, min_qty, override)
        live_state.detectors[symbol] = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
        log_line(log_file, f"ОБНОВЛЁН  {symbol} (min_qty={min_qty}, пороги изменены)")


async def settings_watcher_loop(live_state: LiveState, log_file) -> None:
    previous = dict(live_state.settings)

    while True:
        await asyncio.sleep(SETTINGS_POLL_INTERVAL_SEC)
        current = load_settings()
        if current == previous:
            continue

        if live_state.ws is None:
            continue

        await _apply_settings_diff(live_state, previous, current, log_file)
        previous = current
        live_state.settings = current


async def watchdog_loop(live_state: LiveState, log_file) -> None:
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
        now_ts = datetime.now().timestamp()
        for symbol, symbol_detectors in list(live_state.detectors.items()):
            for detector in symbol_detectors:
                signals, warnings = detector.check_overdue(now_ts)
                for s in signals:
                    log_line(log_file, f"ЗАКРЫТ  {s}")
                for w in warnings:
                    log_line(log_file, f"⚠ ПРОСРОЧКА  {w}")


async def dashboard_loop(live_state: LiveState, shared_state: SharedState) -> None:
    while True:
        now_ts = datetime.now().timestamp()
        shared_state.rows = collect_rows(
            live_state.detectors,
            now_ts,
            min_repeats_to_show=shared_state.min_repeats_show,
            min_repeats_to_show_twap=shared_state.min_repeats_show_twap,
            max_jitter_ms=shared_state.max_jitter_ms,
            max_cv_pct=shared_state.max_cv_pct,
        )
        await asyncio.sleep(DASHBOARD_INTERVAL_SEC)


async def info_tabs_loop(
    live_state: LiveState,
    shared_state: SharedState,
    funding_cache: FundingCache,
    news_cache: NewsCache,
) -> None:
    while True:
        now_ts = datetime.now().timestamp()
        shared_state.arb_rows = [m.snapshot(now_ts) for m in live_state.arb_monitors]

        funding_rows = []
        for row in funding_cache.rows:
            name = FUNDING_NAMES.get(row["secid"], row["secid"])
            rate = row["swap_rate"]
            rate_str = f"{rate:.4f}%" if isinstance(rate, (int, float)) else "н/д"
            funding_rows.append({"name": name, "rate_str": rate_str})
        shared_state.funding_rows = funding_rows
        shared_state.funding_updated_at = (
            funding_cache.updated_at.strftime("%H:%M:%S") if funding_cache.updated_at else ""
        )

        news_items = []
        for row in news_cache.items[:8]:
            news_id = row.get("id")
            news_items.append({
                "title": row.get("title") or "(без заголовка)",
                "time": row.get("published_at") or "",
                "url": f"https://www.moex.com/n{news_id}" if news_id is not None else "",
            })
        shared_state.news_items = news_items
        shared_state.news_updated_at = (
            news_cache.updated_at.strftime("%H:%M:%S") if news_cache.updated_at else ""
        )

        await asyncio.sleep(INFO_TABS_INTERVAL_SEC)


async def _notify_robot(signal, log_file) -> None:
    """Push-уведомление в Telegram намеренно убрано для сигналов по
    роботам — сигнал приходит только когда серия ЗАКРЫЛАСЬ, то есть с
    задержкой, часто когда робот уже неактивен. Актуальное наблюдение —
    через GUI (вкладка "🤖 Роботы")."""
    log_line(log_file, f"НОВЫЙ  {signal}")


async def _notify_arb(signal, log_file) -> None:
    log_line(log_file, f"⚡ {signal}")

    if signal.mode != "ratio_pct" or signal.kind != "divergence":
        # ВРЕМЕННО: bot.py (tg_bot/) пока умеет форматировать только
        # "divergence" в режиме ratio_pct (проценты) — absolute_rub
        # (прострелы/схождения MTLR/MTLRP) в Telegram пока не уходят,
        # см. докстринг модуля.
        return

    await telegram_bot.send_arb_alert({
        "pair_name": signal.pair_name,
        "symbol_a": signal.symbol_a,
        "symbol_b": signal.symbol_b,
        "ratio": signal.value,
        "baseline": signal.baseline,
        "deviation_pct": signal.deviation,
    })


async def run_session(live_state: LiveState, log_file) -> None:
    access_token = get_access_token(REFRESH_TOKEN)
    live_state.access_token = access_token
    print("Токен получен, подключаюсь к WebSocket...")

    async with websockets.connect(WS_URL, ping_interval=15, ping_timeout=10) as ws:
        live_state.ws = ws
        live_state.guid_to_symbol = {}
        live_state.symbol_to_guid = {}

        try:
            all_symbols = build_subscription_symbols(live_state)
            for symbol in sorted(all_symbols):
                await _subscribe_symbol(live_state, symbol)

            print(f"Подписался на {len(all_symbols)} тикеров "
                  f"({len(live_state.detectors)} роботы + "
                  f"{len(live_state.arb_symbols)} арбитраж, с пересечениями), жду сделки...")

            loop = asyncio.get_event_loop()
            deadline = loop.time() + RECONNECT_INTERVAL_SEC

            while loop.time() < deadline:
                timeout = deadline - loop.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(timeout, 0.1))
                except asyncio.TimeoutError:
                    break

                message = json.loads(raw)

                if message.get("httpCode") is not None:
                    if message.get("httpCode") != 200:
                        print(f"Ошибка подписки: {message}")
                    continue

                guid = message.get("guid")
                symbol = live_state.guid_to_symbol.get(guid)
                if symbol is None:
                    continue

                data = message.get("data")
                if not data:
                    continue

                trade = {
                    "qty": data["qty"],
                    "side": data["side"],
                    "timestamp": data["timestamp"],
                }
                for detector in live_state.detectors.get(symbol, []):
                    for signal in detector.on_trade(trade):
                        await _notify_robot(signal, log_file)

                if symbol in live_state.arb_symbols:
                    price = data["price"]
                    ts = data["timestamp"] / 1000.0
                    for monitor in live_state.arb_monitors:
                        signal = monitor.on_trade(symbol, price, ts)
                        if signal is not None:
                            await _notify_arb(signal, log_file)
        finally:
            live_state.ws = None


async def main(shared_state: SharedState) -> None:
    if not REFRESH_TOKEN:
        shared_state.status = "Ошибка: не найден ALOR_REFRESH_TOKEN в .env"
        print(shared_state.status)
        return

    settings = load_settings()
    print(
        f"Активных тикеров: {len(get_active_symbols(settings))} "
        f"(отключённые в ticker_settings.json пропускаются)"
    )

    print("Считаю пороги min_qty по вчерашним данным...")
    shared_state.status = "Считаю пороги min_qty..."
    detectors = build_detectors(settings)
    arb_monitors, arb_symbols = build_arb_monitors()
    live_state = LiveState(settings, detectors, arb_monitors, arb_symbols)

    funding_cache = FundingCache()
    news_cache = NewsCache()

    log_path, log_file = open_log_file()
    print(f"Лог пишется в {log_path}")
    shared_state.status = f"Работает | лог: {log_path.name}"

    watchdog_task = asyncio.create_task(watchdog_loop(live_state, log_file))
    dashboard_task = asyncio.create_task(dashboard_loop(live_state, shared_state))
    settings_task = asyncio.create_task(settings_watcher_loop(live_state, log_file))
    funding_task = asyncio.create_task(funding_refresh_loop(funding_cache))
    news_task = asyncio.create_task(news_refresh_loop(news_cache))
    info_tabs_task = asyncio.create_task(
        info_tabs_loop(live_state, shared_state, funding_cache, news_cache)
    )

    async with telegram_bot:
        try:
            while True:
                try:
                    await run_session(live_state, log_file)
                except ConnectionClosed as e:
                    print(f"Соединение разорвано ({e}), переподключаюсь через 5 сек...")
                    shared_state.status = "Переподключение (разрыв связи)..."
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"Ошибка сессии: {type(e).__name__}: {e}, переподключаюсь через 5 сек...")
                    shared_state.status = f"Ошибка: {e}"
                    await asyncio.sleep(5)
                print("Переподключение (плановое обновление токена)...")
                shared_state.status = "Переподключение (обновление токена)..."
        finally:
            watchdog_task.cancel()
            dashboard_task.cancel()
            settings_task.cancel()
            funding_task.cancel()
            news_task.cancel()
            info_tabs_task.cancel()
            log_file.close()
            print("\nОстановлено, лог сохранён.")
            shared_state.status = "Остановлено"


def start_backend(shared_state: SharedState) -> None:
    try:
        asyncio.run(main(shared_state))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    shared_state = SharedState()

    backend_thread = threading.Thread(target=start_backend, args=(shared_state,), daemon=True)
    backend_thread.start()

    app = RobotDashboardWindow(shared_state)
    app.mainloop()