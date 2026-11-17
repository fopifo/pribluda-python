"""
bot.py — ММВБ Агент (v3.26)
- Фикс MOEX SSL: отдельная сессия с ssl=False для публичных данных ISS.
- Фоновые live-мигалки ОТКЛЮЧЕНЫ. Дневная статистика по кнопке работает.
- Kill-switch, watchdog моста, торговля DRY/LIVE, баланс lite-стиль.
"""
import asyncio
import aiohttp
import ssl
import os
import sys
import json
import time
import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = "f9LHodD0cOK58ifY04m7_DqGlAn07KefMTGq0AJQPLp21PgmHPR7qIy9vhMHTAfnQsS4jthZRz_Lg-7sGXgZ"
BASE_URL = "https://platform-api2.max.ru"
HEADERS = {"Authorization": BOT_TOKEN}
MOEX_HEADERS = {"User-Agent": "MMVB-Agent-Bot/3.26"}
BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

BRIDGE_URL = "http://127.0.0.1:8787"
BRIDGE_TOKEN = "pribluda-local-1"

WATCHDOG_STALE_SEC = 60
WATCHDOG_POLL_SEC = 15
WATCHDOG_COOLDOWN_SEC = 300

SPOOF_ENABLED = False
SPOOF_POLL_SEC = 10
SPOOF_ALERT_COOLDOWN_SEC = 300

TRADE_CONFIG = {
    "MTLR":      {"lots": [1, 99, 100, 200, 300, 500], "label": "🔹 MTLR"},
    "MTLRP":     {"lots": [1, 9, 10, 20, 30, 50],     "label": "🔹 MTLRP"},
    "CNYRUBF":   {"lots": [1, 2, 5],                  "label": "💱 CNYRUBF (вечн)"},
    "CNY-12.26": {"lots": [1, 2, 5],                  "label": "💱 CNY-12.26 (кварт)"},
}

OWNER_ID = 5135149
SPREAD_THRESHOLD = 3.0
POLL_SEC = 30
ALERT_COOLDOWN_SEC = 600

ISS_SECID = {"CNY-12.26": "CRZ6"}
user_states = {}

LAYOUT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
    'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
}

HELP_TEXT = (
    "Я ММВБ Агент — помощник для торгов на Московской Бирже.\n\n"
    "Команды:\n"
    "/start — главное меню\n"
    "/quote — котировка\n"
    "/positions — открытые позиции\n"
    "/balance — финрез (оценка моста)\n"
    "/arba — арбитраж + спреды\n"
    "/spoof — мигалки (дневная статистика)\n"
    "/trade — торговля (dry/live)\n"
    "/kill — 🛑 снять все мои заявки\n\n"
    "Фоновые алерты:\n"
    "• |MTLR - MTLRP| >= 3 руб\n"
    "• watchdog моста (молчит >60с)"
)

MAIN_MENU = [
    {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "📊 Котировка", "payload": "cmd_quote"},
         {"type": "callback", "text": "⚖️ Арбитраж", "payload": "cmd_arba"}],
        [{"type": "callback", "text": "📌 Позиции", "payload": "cmd_positions"},
         {"type": "callback", "text": "💰 Баланс", "payload": "cmd_balance"}],
        [{"type": "callback", "text": "🛒 Торговля", "payload": "cmd_trade"},
         {"type": "callback", "text": "🕵️ Мигалки", "payload": "cmd_spoof"}],
        [{"type": "callback", "text": "🛑 Снять все заявки", "payload": "cmd_kill"},
         {"type": "callback", "text": "❓ Справка", "payload": "cmd_help"}]
    ]}}
]

ARBA_MENU = [
    {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "🔹 MTLR / MTLRP", "payload": "cmd_arba_mtlr"},
         {"type": "callback", "text": "💱 CNYRUBF / Кварт.", "payload": "cmd_arba_cny"}],
        [{"type": "callback", "text": "✍️ Свои тикеры", "payload": "cmd_arba_manual"},
         {"type": "callback", "text": "◀️ Назад", "payload": "cmd_back"}]
    ]}}
]

TAG_RU = {
    "FLASH": "ставит крупную заявку и быстро снимает (классический спуфинг)",
    "PULL": "держит крупную заявку и кратковременно убирает её",
    "MID": "держит заявку примерно половину периода",
}

def fix_layout(text: str) -> str:
    return "".join(LAYOUT_MAP.get(c, c) for c in text.lower()).upper().strip()

def human_sec(s):
    if s is None: return "?"
    return f"{int(s // 60)}м{int(s % 60)}с" if s >= 60 else f"{s:.0f}с"

def point_mult(sym):
    return 1000 if (sym == "CNYRUBF" or sym.startswith("CNY-")) else 1

def platform_of(sym):
    if sym in ("CNYRUB_TOM", "CNY000000TOD") or sym.endswith("_TOM") or sym.endswith("TOD"): return "Валюта"
    if sym == "CNYRUBF" or sym.startswith("CNY-"): return "Фьючерсы"
    return "Акции"

def board_of(sym):
    p = platform_of(sym)
    return "RFUD" if p == "Фьючерсы" else ("CETS" if p == "Валюта" else "TQBR")

def iss_ticker(sym):
    return ISS_SECID.get(sym, sym)

def create_ssl_context():
    ctx = ssl.create_default_context()
    certs_dir = os.path.join(os.path.dirname(__file__), "certs")
    for name in ("russian_trusted_root_ca.pem", "russian_trusted_sub_ca.pem"):
        p = os.path.join(certs_dir, name)
        if os.path.exists(p):
            try: ctx.load_verify_locations(p)
            except Exception: pass
    return ctx

def read_bridge_state():
    p = DATA_DIR / "bridge_state.json"
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def format_positions(state):
    if not state: return "⚠️ Мост не отдаёт состояние."
    syms = state.get("symbols", {})
    open_syms = {s: i for s, i in syms.items() if i.get("position", 0) != 0}
    age = (time.time() * 1000 - state.get("updated_ms", 0)) / 1000
    if not open_syms: return f"📌 Открытых позиций нет. (обновлено {age:.0f}с)"

    def leg(s):
        i = open_syms[s]
        p = i["position"]
        side = "лонг" if p > 0 else "шорт"
        return f"   {s}: {side} {abs(p):g} @ {i.get('pos_price', 0):g}"

    def mid(s):
        i = syms.get(s, {})
        bb = i.get("best_bid", 0); ba =i.get("best_ask", 0)
        return (bb + ba) / 2 if bb > 0 and ba > 0 else None

    lines = [f"📌 **Открытые позиции** (обновлено {age:.0f}с)\n"]
    mech = [s for s in ("MTLR", "MTLRP") if s in open_syms]
    if mech:
        lines.append("🔹 **Мечел**")
        lines += [leg(s) for s in mech]
        a, b = mid("MTLR"), mid("MTLRP")
        if a and b: lines.append(f"   спред MTLR−MTLRP: {a - b:+.2f}")
        lines.append("")
    cny = [s for s in open_syms if s == "CNYRUBF" or s.startswith("CNY-")]
    if cny:
        lines.append("💱 **Юань**")
        lines += [leg(s) for s in sorted(cny)]
        perp = mid("CNYRUBF")
        q = [mid(s) for s in sorted(cny) if s.startswith("CNY-")]
        if perp and q and q[0]: lines.append(f"   спред кварт−вечн: {q[0] - perp:+.4f}")
        lines.append("")
    for s in sorted([s for s in open_syms if s not in mech and s not in cny]):
        lines.append(leg(s).strip())
    return "\n".join(lines).rstrip()

async def format_balance(session, state):
    if not state: return "⚠️ Мост не отдаёт состояние."
    age = (time.time() * 1000 - state.get("updated_ms", 0)) / 1000
    syms = state.get("symbols", {})
    fin = state.get("fin", {})
    lines = [f"💰 **Финрез за сегодня (оценка моста)** (обновлено {age:.0f}с)\n"]
    plats = {}
    for s, i in syms.items():
        pos = i.get("position", 0)
        realized = i.get("realized", 0.0)
        bb, ba = i.get("best_bid", 0), i.get("best_ask", 0)
        cur = (bb + ba) / 2 if bb > 0 and ba > 0 else None
        d, _ = await get_moex_data(session, iss_ticker(s), board_of(s))
        if not d: continue
        if cur is None: cur = d.get("last")
        prev = d.get("prev_settle") if board_of(s) == "RFUD" else d.get("prev")
        if prev is None: prev = d.get("prev")
        if cur is None or prev is None: continue
        if pos == 0 and abs(realized) < 0.005: continue
        daily = realized + (cur - prev) * pos * point_mult(s)
        plats.setdefault(platform_of(s), []).append((s, pos, daily))
    grand = 0.0
    for plat in ("Акции", "Фьючерсы", "Валюта"):
        rows = plats.get(plat)
        if not rows: continue
        lines.append(f"**{plat}**")
        sub = 0.0
        for s, pos, daily in sorted(rows):
            sub += daily
            e = "🟢" if daily >= 0 else "🔴"
            lines.append(f"   {e} {s}: поз {pos:+g} → фин.рез **{daily:+,.2f} ₽**")
        lines.append(f"   ИТОГО {plat}: **{sub:+,.2f} ₽**\n")
        grand += sub
    ge = "🟢" if grand >= 0 else "🔴"
    lines.append(f"💵 **ОБЩИЙ финрез: {ge} {grand:+,.2f} ₽**")
    if fin:
        lines.append("\n_Данные привода (комиссии/активы):_")
        ct = 0.0
        for slot, d in fin.items():
            c = d.get("Commission", 0.0); ct += c
            lines.append(f"   {slot}: комиссия {c:,.2f} ₽ | активов {d.get('TotalAssets', 0):,.0f} ₽ | свободно {d.get('FreeAssets', 0):,.0f} ₽")
        lines.append(f"   Комиссия всего: {ct:,.2f} ₽")
    lines.append("\n_= реализованный сегодня + (тек.цена − вчерашний сеттлемент/WAP) × позиция × стоимость пункта._")
    lines.append("_Точный финрез многодневной позиции = в lite-invest._")
    return "\n".join(lines)

def format_spoof(data):
    lines = ["🕵️ **Мигалки — дневная статистика**", ""]
    cands = data.get("candidates", [])
    if not cands:
        lines.append("Сейчас явных роботов-«мигалок» не видно.")
        lines.append("")
    else:
        for c in cands[:4]:
            if c["side"] == "bids":
                head = f"🟢⬆️ **{c['sym']}** — ПОДДЕРЖКА (крупные покупки) по {c['price']}"
            else:
                head = f"🔴⬇️ **{c['sym']}** — СОПРОТИВЛЕНИЕ (крупные продажи) по {c['price']}"
            lines.append(head)
            lines.append(f"   Повторяет каждые ~{human_sec(c['period_s'])}; замечено циклов: {c['n']}, регулярность {c['reg']:.0%}.")
            lines.append(f"   Держит заявку ~{human_sec(c['life_s'])}. {TAG_RU.get(c['tag'], c['tag'])}.")
            lines.append("")
    act = data.get("activity", {})
    if act:
        top = sorted(act.items(), key=lambda kv: -kv[1]["rate"])[:3]
        lines.append("📈 Активность рынка: " + "; ".join(f"{s} {v['rate']}/с" for s, v in top))
    lines.append("\n_🟢⬆️ поддержка = цена скорее отскочит ВВЕРХ; 🔴⬇️ сопротивление = скорее ВНИЗ._")
    lines.append("_Фоновые live-мигалки отключены._")
    return "\n".join(lines)

async def api_get(session, path, params=None):
    async with session.get(BASE_URL + path, headers=HEADERS, params=params) as resp:
        try: return resp.status, await resp.json()
        except: return resp.status, {"raw": await resp.text()}

async def send_message(session, text, user_id=None, chat_id=None, attachments=None):
    params = {"user_id": int(user_id)} if user_id is not None else {"chat_id": int(chat_id)}
    payload = {"text": str(text), "format": "markdown"}
    if attachments: payload["attachments"] = attachments
    async with session.post(BASE_URL + "/messages", headers=HEADERS, params=params, json=payload) as resp:
        status = resp.status
        try: data = await resp.json()
        except: data = {"raw": await resp.text()}
        if status != 200 or data.get("success") is False:
            print(f"[SEND ERROR] status={status} resp={data}")
        return data

async def answer_callback(session, callback_id: str):
    async with session.post(BASE_URL + "/answers", headers=HEADERS, json={"callback_id": str(callback_id)}) as resp:
        return await resp.json()

def fmt_val(v):
    if v is None: return "Н/Д"
    if v >= 1_000_000_000: return f"{v / 1_000_000_000:.2f} млрд ₽"
    if v >= 1_000_000: return f"{v / 1_000_000:.2f} млн ₽"
    return f"{v:,.0f} ₽"

async def get_moex_data(session_moex, ticker: str, board: str = "TQBR"):
    engine = "stock" if board in ["TQBR", "CETS"] else "futures"
    market = "shares" if board == "TQBR" else ("selt" if board == "CETS" else "forts")
    url = f"https://iss.moex.com/iss/engines/{engine}/markets/{market}/boards/{board}/securities/{ticker}.json"
    params = {
        "iss.only": "marketdata,securities",
        "marketdata.columns": "SECID,LAST,LOW,HIGH,VALTODAY,SWAPRATE,SWAPRATE_CURR,SETTLEPRICE",
        "securities.columns": "SECID,PREVWAPRICE,PREVPRICE,PREVSETTLE,LOTSIZE"
    }
    try:
        # Используем session_moex, у которой ssl=False
        async with session_moex.get(url, params=params, headers=MOEX_HEADERS) as resp:
            if resp.status != 200: return None, f"❌ Ошибка биржи ({resp.status})"
            data = await resp.json()
            md = data.get("marketdata", {}); md_rows = md.get("data", [])
            last = low = high = val = swap = swap_curr = settle = None
            if md_rows:
                q = dict(zip(md.get("columns", []), md_rows[0]))
                last = q.get("LAST"); low = q.get("LOW"); high = q.get("HIGH")
                val = q.get("VALTODAY"); swap = q.get("SWAPRATE"); swap_curr = q.get("SWAPRATE_CURR")
                settle = q.get("SETTLEPRICE")
            sec = data.get("securities", {}); sec_rows = sec.get("data", [])
            prev = prev_settle = lotsize = None
            if sec_rows:
                sq = dict(zip(sec.get("columns", []), sec_rows[0]))
                prev = sq.get("PREVWAPRICE") or sq.get("PREVPRICE")
                prev_settle = sq.get("PREVSETTLE")
                lotsize = sq.get("LOTSIZE") or 1
            if last is not None:
                change_abs = last - prev if prev else 0
                change_pct = (change_abs / prev * 100) if prev else 0
                arrow = "🟢" if change_abs >= 0 else "🔴"
                price_str = f"**{last}** ₽"
                note = ""
            elif prev is not None:
                price_str = f"**{prev}** ₽"
                note = "\n_(⚠️ Сессия закрыта)_"
                arrow = ""; change_pct = 0
            else:
                return None, f"⚠️ По **{ticker}** нет данных."
            text = (f"📊 **{ticker}** ({board})\n\n"
                    f"💰 Цена: {price_str}  {arrow} {change_pct:+.2f}%{note}\n"
                    f"📉 Мин: {low if low is not None else 'Н/Д'} ₽  |  📈 Макс: {high if high is not None else 'Н/Д'} ₽\n"
                    f"💵 Оборот: {fmt_val(val)}")
            return {"last": last, "prev": prev, "prev_settle": prev_settle, "swap": swap,
                    "swap_curr": swap_curr, "settle": settle, "lotsize": lotsize}, text
    except Exception as e:
        print(f"[MOEX ERROR] {e}")
        return None, f"❌ Ошибка: {e}"

async def get_price(session_moex, ticker, board="TQBR"):
    d, _ = await get_moex_data(session_moex, ticker, board)
    return (d["last"] or d["prev"]) if d else None

async def get_nearest_cny_futures(session_moex):
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json?iss.only=securities&securities.columns=SECID,SECNAME,LASTTRADEDATE"
    try:
        async with session_moex.get(url, headers=MOEX_HEADERS) as resp:
            data = await resp.json()
            rows = data.get("securities", {}).get("data", [])
            cols = data.get("securities", {}).get("columns", [])
            active = []; today = datetime.now().date()
            for row in rows:
                d = dict(zip(cols, row))
                secid = str(d.get("SECID", ""))
                if secid.startswith("CR") and len(secid) <= 5 and secid not in ["CRCLF", "CRDOF", "CRWDF", "CRWVF"]:
                    try:
                        ltd = datetime.strptime(d.get("LASTTRADEDATE", "2100-01-01"), "%Y-%m-%d").date()
                        if ltd >= today: active.append((secid, ltd, d.get("SECNAME")))
                    except ValueError: continue
            if not active: return None, "Не найдено квартальных фьючерсов на юань."
            active.sort(key=lambda x: x[1])
            n = active[0]
            return n[0], f"{n[0]} (эксп: {n[1].strftime('%d.%m.%Y')})"
    except Exception as e:
        return None, f"Ошибка: {e}"

async def spreads_text(session_moex):
    m1 = await get_price(session_moex, "MTLR")
    m2 = await get_price(session_moex, "MTLRP")
    cny = await get_price(session_moex, "CNYRUBF", "RFUD")
    qt, _ = await get_nearest_cny_futures(session_moex)
    q = await get_price(session_moex, qt, "RFUD") if qt else None
    lines = ["📐 **Спреды сейчас:**"]
    if m1 is not None and m2 is not None: lines.append(f"🔹 Мечел MTLR−MTLRP: **{m1 - m2:+.2f} ₽**")
    if q is not None and cny is not None: lines.append(f"💱 Юань кварт−вечн: **{q - cny:+.4f} ₽**")
    return "\n".join(lines)

async def get_cny_arbitrage(session_moex):
    perp, pt = await get_moex_data(session_moex, "CNYRUBF", "RFUD")
    if perp is None: return f"⚠️ Не удалось получить CNYRUBF.\n{pt}"
    qt, qn = await get_nearest_cny_futures(session_moex)
    if not qt: return f"⚠️ {qn}"
    qd, qt2 = await get_moex_data(session_moex, qt, "RFUD")
    if qd is None: return f"⚠️ Не удалось получить {qt}.\n{qt2}"
    p = perp["last"] or perp["settle"] or perp["prev"]
    q = qd["last"] or qd["settle"] or qd["prev"]
    lot = perp.get("lotsize", 1000)
    spread = q - p if (q and p) else None
    spread_str = f"**{spread:.4f} ₽**" if spread is not None else "Н/Д"
    swap = perp.get("swap_curr") or perp.get("swap")
    funding = f"**{swap * lot:.2f} ₽** (ставка {swap})" if swap is not None else "Н/Д"
    return (f"💱 **Юань: вечный vs квартальный**\n\n"
            f"🔄 CNYRUBF (лот={lot}): **{p}** ₽\n   Фандинг/лот: {funding}\n\n"
            f"📅 {qt} ({qn}): **{q}** ₽\n\n"
            f"📏 Спред (кварт−вечн): {spread_str}")

async def run_spoof(session, user_id):
    await send_message(session, "⏳ Сканирую стакан (~5с)...", user_id=user_id)
    date = datetime.now(MSK).strftime("%Y-%m-%d")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(BASE / "research" / "orderbook_audit.py"), date,
        "--json", "--thr", "1000", "--min-n", "6", "--min-reg", "0.8",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=str(BASE))
    out, _ = await proc.communicate()
    txt = out.decode("utf-8", "ignore").strip()
    data = None
    for line in reversed(txt.splitlines()):
        if line.strip().startswith("{"):
            try: data = json.loads(line.strip())
            except: data = None
            break
    if data is None:
        await send_message(session, "⚠️ Не удалось прочитать результат аудита.", user_id=user_id, attachments=MAIN_MENU)
        return
    await send_message(session, format_spoof(data), user_id=user_id, attachments=MAIN_MENU)

async def bridge_post(session, path, body, timeout=8):
    headers = {"Content-Type": "application/json"}
    try:
        async with session.post(BRIDGE_URL + path, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return resp.status, await resp.json()
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}

async def bridge_get_json(session, path, timeout=3):
    try:
        async with session.get(BRIDGE_URL + path, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200: return None
            return await resp.json()
    except Exception:
        return None

def make_spoof_msg(level):
    sym = level["sym"]; side = level["side"]; price = level["price"]
    amt = level.get("amount", 0); hold = level.get("hold_s", 0); cycles = level.get("cycles", 1)
    last_kind = level.get("last_alert")
    if side == "bids": emoji = "🟢⬆️"; ru = "ПОДДЕРЖКА"
    else: emoji = "🔴⬇️"; ru = "СОПРОТИВЛЕНИЕ"
    hint = "цена скорее отскочит ВВЕРХ" if side == "bids" else "цена скорее отскочит ВНИЗ"
    if last_kind == "bot":
        return (f"{emoji} **{sym}: РОБОТ {ru}**\n"
                f"Цена: **{price}**, объём: **{amt:g}** лотов\n"
                f"Держится: {human_sec(hold)}, циклов: {cycles}\n"
                f"_{hint} от этой линии._")
    elif last_kind == "gone":
        return (f"💨 **{sym}: {ru} УШЛА**\n"
                f"Цена: **{price}**, объём был: **{amt:g}** лотов\n"
                f"_Окно для входа: крупной стенки больше нет._")
    else:
        return (f"{emoji} **{sym}: {ru}**\n"
                f"Цена: **{price}**, объём: **{amt:g}** лотов\n"
                f"Держится: {human_sec(hold)}, циклов: {cycles}\n"
                f"_{hint} от этой линии._")

async def monitor_spoof(session):
    if not SPOOF_ENABLED: return
    seen = {}
    while True:
        try:
            data = await bridge_get_json(session, "/spoof_live")
            if data and isinstance(data, dict):
                levels = data.get("levels", []) or []
                now = time.time()
                for lvl in levels:
                    sym = lvl.get("sym"); side = lvl.get("side"); price = lvl.get("price")
                    kind = lvl.get("last_alert") or "wall"
                    if sym is None or side is None or price is None: continue
                    key = f"{sym}|{side}|{price}|{kind}"
                    prev = seen.get(key)
                    if prev is None or (now - prev[1]) > SPOOF_ALERT_COOLDOWN_SEC:
                        seen[key] = (kind, now)
                        await send_message(session, make_spoof_msg(lvl), user_id=OWNER_ID)
                for k, (knd, ts) in list(seen.items()):
                    if now - ts > SPOOF_ALERT_COOLDOWN_SEC * 2: del seen[k]
        except Exception as e:
            print(f"[SPOOF-MON ERROR] {e}")
        await asyncio.sleep(SPOOF_POLL_SEC)

def trade_pick_sym_menu():
    buttons = []
    for sym, cfg in TRADE_CONFIG.items():
        buttons.append([{"type": "callback", "text": cfg["label"], "payload": f"t:sym:{sym}"}])
    buttons.append([{"type": "callback", "text": "◀️ В главное меню", "payload": "cmd_back"}])
    return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]

def trade_pick_act_menu(sym):
    return [{"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "🟢 Купить", "payload": f"t:act:{sym}:buy"}],
        [{"type": "callback", "text": "🔴 Продать", "payload": f"t:act:{sym}:sell"}],
        [{"type": "callback", "text": "🔄 Закрыть позицию", "payload": f"t:close:{sym}"}],
        [{"type": "callback", "text": "◀️ Инструменты", "payload": "t:pick"}]
    ]}}]

def trade_pick_lot_menu(sym, side):
    cfg = TRADE_CONFIG[sym]
    emoji = "🟢" if side == "buy" else "🔴"
    buttons = []; row = []
    for q in cfg["lots"]:
        row.append({"type": "callback", "text": f"{emoji} {q}", "payload": f"t:lot:{sym}:{side}:{q}"})
        if len(row) == 3: buttons.append(row); row = []
    if row: buttons.append(row)
    buttons.append([{"type": "callback", "text": f"◀️ {cfg['label']}", "payload": f"t:sym:{sym}"}])
    return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]

def trade_preview_text(sym, side, qty, close=False):
    state = read_bridge_state()
    if not state: return "⚠️ Мост не отдаёт состояние."
    info = state.get("symbols", {}).get(sym)
    if not info: return f"⚠️ Нет данных по {sym}."
    if not info.get("ready"): return f"⚠️ Слой {sym} не готов (нет стакана)."
    bb = info.get("best_bid", 0); ba = info.get("best_ask", 0)
    price = ba if side == "buy" else bb
    if price <= 0: return f"⚠️ Нет цены {'ask' if side == 'buy' else 'bid'} по {sym}."
    side_ru = "ПОКУПКА" if side == "buy" else "ПРОДАЖА"
    emoji = "🟢" if side == "buy" else "🔴"
    cur_pos = info.get("position", 0)
    header = "🔄 **Закрытие позиции**" if close else f"{emoji} **Заявка**"
    return (f"{header}\n\n"
            f"**{side_ru}: {sym} × {qty:g} лот**\n"
            f"Цена (best {'ask' if side == 'buy' else 'bid'}): **{price:g} ₽**\n"
            f"Сумма: **{price * qty:,.2f} ₽**\n"
            f"Текущая позиция: **{cur_pos:+g}**\n\n"
            f"🔸 **✅ DRY** = только в лог, на биржу НЕ идёт.\n"
            f"🔸 **⚡ LIVE** = реальная заявка на сервер!")

def confirm_menu(side, close=False):
    return [{"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "✅ DRY (в лог)", "payload": "t:ok"}],
        [{"type": "callback", "text": "⚡ LIVE (на биржу)", "payload": "t:live"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "cmd_back"}]
    ]}}]

def kill_menu():
    buttons = []
    for sym in TRADE_CONFIG:
        buttons.append([{"type": "callback", "text": f"🛑 {sym}", "payload": f"k:sym:{sym}"}])
    buttons.append([{"type": "callback", "text": "🛑 ВСЕ СЛОИ", "payload": "k:all"}])
    buttons.append([{"type": "callback", "text": "◀️ В главное меню", "payload": "cmd_back"}])
    return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]

def kill_confirm_menu(target):
    label = "ВСЕ" if target == "*" else target
    return [{"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": f"✅ Снять заявки {label}", "payload": f"k:go:{target}"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "cmd_back"}]
    ]}}]

async def do_trade_confirm(session, user_id, live):
    st = user_states.get(user_id, {})
    if st.get("step") != "TRADE_PREVIEW":
        await send_message(session, "⚠️ Превью истекло. Начни заново.", user_id=user_id, attachments=MAIN_MENU); return
    sym = st.get("sym"); side = st.get("side"); qty = st.get("qty"); close = st.get("close", False)
    state = read_bridge_state()
    if not state:
        await send_message(session, "⚠️ Мост не отдаёт состояние.", user_id=user_id, attachments=MAIN_MENU); return
    info = state.get("symbols", {}).get(sym)
    if not info or not info.get("ready"):
        await send_message(session, f"⚠️ Слой {sym} не готов.", user_id=user_id, attachments=MAIN_MENU); return
    bb = info.get("best_bid", 0); ba = info.get("best_ask", 0)
    price = ba if side == "buy" else bb
    if price <= 0:
        await send_message(session, f"⚠️ Нет цены по {sym}.", user_id=user_id, attachments=MAIN_MENU); return
    body = {"token": BRIDGE_TOKEN, "id": f"bot-{uuid.uuid4().hex}", "symbol": sym, "side": side, "qty": qty, "type": "limit", "price": price}
    if close: body["close_position"] = True
    if live: body["live"] = True
    status, data = await bridge_post(session, "/order", body)
    if status != 200 or not data.get("ok"):
        err = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
        await send_message(session, f"❌ Мост отклонил: {err}", user_id=user_id, attachments=MAIN_MENU); return
    if data.get("duplicate"):
        await send_message(session, "⚠️ Команда уже была отправлена (дубль).", user_id=user_id, attachments=MAIN_MENU); return
    user_states[user_id] = {"step": "IDLE"}
    side_ru = "ПОКУПКА" if side == "buy" else "ПРОДАЖА"
    emoji = "🟢" if side == "buy" else "🔴"
    if live:
        acc = data.get("accepted")
        head = "⚡ **LIVE: заявка отправлена на сервер**" if acc else "⚡ **LIVE: сервер ОТКЛОНИЛ заявку**"
        note = data.get("note", "")
    else:
        head = f"{emoji} **DRY-RUN: команда записана в лог**"
        note = "Заявка НЕ отправлена на биржу."
    await send_message(session, f"{head}\n\n{side_ru}: {data.get('symbol')} × {data.get('qty')} @ {data.get('price')} ₽\n\n🔸 {note}\n🔸 Лог: `data/trade_commands.jsonl`.", user_id=user_id, attachments=MAIN_MENU)

async def do_kill(session, user_id, target):
    body = {"token": BRIDGE_TOKEN, "symbol": target}
    status, data = await bridge_post(session, "/cancel_all", body)
    if status != 200 or not data.get("ok"):
        err = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
        await send_message(session, f"❌ Мост отклонил cancel_all: {err}", user_id=user_id, attachments=MAIN_MENU); return
    n = data.get("cancelled", 0)
    details = ", ".join(data.get("details", [])) or "—"
    head = f"🛑 **Заявок снято: {n}**" if n > 0 else "🛑 Снимать нечего (нет активных заявок)"
    label = "ВСЕ слои" if target == "*" else target
    await send_message(session, f"{head}\n\nСлоёв обработано: {label}\nДетали: {details}", user_id=user_id, attachments=MAIN_MENU)

async def monitor_mtlr_spread(session_moex):
    prev_mag = None; last_alert = 0.0
    while True:
        try:
            d1, _ = await get_moex_data(session_moex, "MTLR", "TQBR")
            d2, _ = await get_moex_data(session_moex, "MTLRP", "TQBR")
            if d1 and d2:
                p1 = d1["last"] or d1["prev"]; p2 = d2["last"] or d2["prev"]
                if p1 is not None and p2 is not None:
                    spread = p1 - p2; mag = abs(spread); now = time.time()
                    cross = prev_mag is None or prev_mag < SPREAD_THRESHOLD
                    if mag >= SPREAD_THRESHOLD and (cross or now - last_alert > ALERT_COOLDOWN_SEC):
                        direction = "MTLR дороже MTLRP" if spread > 0 else "MTLRP дороже MTLR"
                        msg = (f"🚨 **Раздвижка Мечел >= {SPREAD_THRESHOLD:.0f}₽**\n\n"
                               f"MTLR: **{p1}** ₽\nMTLRP: **{p2}** ₽\n"
                               f"Спред: **{spread:+.2f}** ₽ (|{mag:.2f}|)\n{direction}")
                        await send_message(session_moex, msg, user_id=OWNER_ID)
                        last_alert = now
                    prev_mag = mag
        except Exception as e:
            print(f"[MONITOR ERROR] {e}")
        await asyncio.sleep(POLL_SEC)

async def watchdog_bridge(session):
    last_alert = 0.0
    while True:
        try:
            st = read_bridge_state()
            if st:
                age = (time.time() * 1000 - st.get("updated_ms", 0)) / 1000
                if age > WATCHDOG_STALE_SEC:
                    now = time.time()
                    if now - last_alert > WATCHDOG_COOLDOWN_SEC:
                        await send_message(session, f"⚠️ **Мост не отвечает {age:.0f}с**\n\nbridge_state.json не обновлялся дольше {WATCHDOG_STALE_SEC}с.\nВозможно, ноут/мост упал. Проверь `PbSdkConsole`.", user_id=OWNER_ID)
                        last_alert = now
        except Exception as e:
            print(f"[WATCHDOG ERROR] {e}")
        await asyncio.sleep(WATCHDOG_POLL_SEC)

async def handle_update(session_max, session_moex, upd):
    try:
        ut = upd.get("update_type")
        if ut == "message_callback":
            cb = upd.get("callback", {}); callback_id = cb.get("callback_id"); payload = cb.get("payload", "")
            user_id = (cb.get("user") or {}).get("user_id")
            print(f"[IN] callback payload={payload!r}")
            await answer_callback(session_max, callback_id)
            
            if payload == "cmd_quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                await send_message(session_max, "✍️ Введите тикер (SBER, GAZP, MTLR...):", user_id=user_id)
            elif payload == "cmd_positions":
                await send_message(session_max, format_positions(read_bridge_state()), user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_balance":
                await send_message(session_max, await format_balance(session_moex, read_bridge_state()), user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_spoof":
                await run_spoof(session_max, user_id)
            elif payload == "cmd_kill":
                await send_message(session_max, "🛑 **Снять заявки**\n\nВыбери слой (или ВСЕ):", user_id=user_id, attachments=kill_menu())
            elif payload.startswith("k:sym:"):
                await send_message(session_max, f"Снять все заявки по **{payload.split(':', 2)[2]}**?", user_id=user_id, attachments=kill_confirm_menu(payload.split(':', 2)[2]))
            elif payload == "k:all":
                await send_message(session_max, "Снять заявки по **ВСЕМ** слоям?", user_id=user_id, attachments=kill_confirm_menu("*"))
            elif payload.startswith("k:go:"):
                await do_kill(session_max, user_id, payload.split(":", 2)[2])
            elif payload in ("cmd_trade", "t:pick"):
                user_states[user_id] = {"step": "TRADE_PICK_SYM"}
                await send_message(session_max, "🛒 **Торговля**\n\nВыберите инструмент:", user_id=user_id, attachments=trade_pick_sym_menu())
            elif payload.startswith("t:sym:"):
                sym = payload.split(":", 2)[2]
                if sym not in TRADE_CONFIG:
                    await send_message(session_max, "⚠️ Неизвестный инструмент.", user_id=user_id, attachments=MAIN_MENU); return
                user_states[user_id] = {"step": "td", "sym": sym}
                cfg = TRADE_CONFIG[sym]; state = read_bridge_state()
                pos = state.get("symbols", {}).get(sym, {}).get("position", 0) if state else 0
                side_ru = "лонг" if pos > 0 else ("шорт" if pos < 0 else "нет позиции")
                await send_message(session_max, f"**{cfg['label']}**\nТекущая позиция: **{pos:+g}** ({side_ru})\n\nВыберите действие:", user_id=user_id, attachments=trade_pick_act_menu(sym))
            elif payload.startswith("t:act:"):
                parts = payload.split(":", 3); sym, side = parts[2], parts[3]
                if sym not in TRADE_CONFIG or side not in ("buy", "sell"):
                    await send_message(session_max, "⚠️ Неверные параметры.", user_id=user_id, attachments=MAIN_MENU); return
                user_states[user_id] = {"step": "TRADE_PICK_LOT", "sym": sym, "side": side}
                await send_message(session_max, f"**{TRADE_CONFIG[sym]['label']}** — {'купить' if side == 'buy' else 'продать'}\n\nВыберите объём (лоты):", user_id=user_id, attachments=trade_pick_lot_menu(sym, side))
            elif payload.startswith("t:close:"):
                sym = payload.split(":", 2)[2]
                if sym not in TRADE_CONFIG:
                    await send_message(session_max, "⚠️ Неизвестный инструмент.", user_id=user_id, attachments=MAIN_MENU); return
                state = read_bridge_state()
                if not state:
                    await send_message(session_max, "⚠️ Мост не отдаёт состояние.", user_id=user_id, attachments=MAIN_MENU); return
                cur_pos = state.get("symbols", {}).get(sym, {}).get("position", 0)
                if cur_pos == 0:
                    await send_message(session_max, f"⚠️ По {sym} нет открытой позиции — нечего закрывать.", user_id=user_id, attachments=trade_pick_act_menu(sym)); return
                user_states[user_id] = {"step": "TRADE_PREVIEW", "sym": sym, "side": "sell" if cur_pos > 0 else "buy", "qty": abs(cur_pos), "close": True}
                await send_message(session_max, trade_preview_text(sym, "sell" if cur_pos > 0 else "buy", abs(cur_pos), close=True), user_id=user_id, attachments=confirm_menu("sell" if cur_pos > 0 else "buy", close=True))
            elif payload.startswith("t:lot:"):
                parts = payload.split(":", 4); sym, side, qs = parts[2], parts[3], parts[4]
                try: qty = int(qs)
                except: qty = 0
                if sym not in TRADE_CONFIG or side not in ("buy", "sell") or qty <= 0:
                    await send_message(session_max, "⚠️ Неверные параметры.", user_id=user_id, attachments=MAIN_MENU); return
                user_states[user_id] = {"step": "TRADE_PREVIEW", "sym": sym, "side": side, "qty": qty, "close": False}
                await send_message(session_max, trade_preview_text(sym, side, qty, close=False), user_id=user_id, attachments=confirm_menu(side, close=False))
            elif payload == "t:ok":
                await do_trade_confirm(session_max, user_id, live=False)
            elif payload == "t:live":
                await do_trade_confirm(session_max, user_id, live=True)
            elif payload == "cmd_arba":
                await send_message(session_max, (await spreads_text(session_moex)) + "\n\n⚖️ **Выберите связку:**", user_id=user_id, attachments=ARBA_MENU)
            elif payload == "cmd_arba_mtlr":
                await send_message(session_max, "⏳ Считаю MTLR / MTLRP...", user_id=user_id)
                d1, t1 = await get_moex_data(session_moex, "MTLR", "TQBR")
                d2, t2 = await get_moex_data(session_moex, "MTLRP", "TQBR")
                sp = f"\n\n📐 **Спред MTLR−MTLRP: {d1['last'] - d2['last']:+.2f} ₽**" if d1 and d2 and d1.get('last') and d2.get('last') else ""
                await send_message(session_max, f"⚖️ **MTLR / MTLRP**{sp}\n\n{t1}\n\n{t2}", user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_cny":
                await send_message(session_max, "⏳ Загружаю юань...", user_id=user_id)
                await send_message(session_max, await get_cny_arbitrage(session_moex), user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_manual":
                user_states[user_id] = {"step": "AWAITING_ARBA_T1"}
                await send_message(session_max, "✍️ Введите **первый** тикер:", user_id=user_id)
            elif payload == "cmd_help":
                await send_message(session_max, HELP_TEXT, user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_back":
                user_states[user_id] = {"step": "IDLE"}
                await send_message(session_max, "🏠 **Главное меню:**", user_id=user_id, attachments=MAIN_MENU)
            return
            
        if ut == "message_created":
            msg = upd.get("message") or {}; sender = msg.get("sender") or {}; recipient = msg.get("recipient") or {}
            if sender.get("is_bot"): return
            text = (msg.get("body") or {}).get("text") or ""
            user_id = sender.get("user_id"); chat_id = recipient.get("chat_id"); chat_type = recipient.get("chat_type")
            tu = user_id if chat_type == "dialog" else None; tc = chat_id if chat_type != "dialog" else None
            print(f"[IN] {chat_type} user={user_id} text={text!r}")
            t = text.strip(); tl = t.lower(); reply = None; att = None
            st = user_states.get(user_id)
            
            if st and st.get("step") == "AWAITING_TICKER":
                user_states[user_id] = {"step": "IDLE"}
                if tl in ["/help", "/start", "/arba", "/quote", "/positions", "/spoof", "/balance", "/trade", "/kill"]:
                    reply = "Отмена ввода."; att = MAIN_MENU
                else:
                    _, reply = await get_moex_data(session_moex, fix_layout(t)); att = MAIN_MENU
            elif st and st.get("step") == "AWAITING_ARBA_T1":
                user_states[user_id] = {"step": "AWAITING_ARBA_T2", "t1": fix_layout(t)}
                reply = f"Принято: **{fix_layout(t)}**. Второй тикер:"
            elif st and st.get("step") == "AWAITING_ARBA_T2":
                t2 = fix_layout(t); t1 = st.get("t1", ""); user_states[user_id] = {"step": "IDLE"}
                d1, r1 = await get_moex_data(session_moex, t1); d2, r2 = await get_moex_data(session_moex, t2)
                sp = f"\n\n📐 **Спред {t1}−{t2}: {d1['last'] - d2['last']:+.4f} ₽**" if d1 and d2 and d1.get('last') and d2.get('last') else ""
                reply = f"⚖️ **{t1} / {t2}**{sp}\n\n{r1}\n\n{r2}"; att = MAIN_MENU
            elif tl in ("/start", "/help"):
                reply = "🏠 **Главное меню:**"; att = MAIN_MENU
            elif tl == "/quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}; reply = "✍️ Введите тикер:"
            elif tl == "/positions":
                reply = format_positions(read_bridge_state()); att = MAIN_MENU
            elif tl == "/balance":
                reply = await format_balance(session_moex, read_bridge_state()); att = MAIN_MENU
            elif tl == "/trade":
                user_states[user_id] = {"step": "TRADE_PICK_SYM"}; reply = "🛒 **Торговля**\n\nВыберите инструмент:"; att = trade_pick_sym_menu()
            elif tl == "/kill":
                reply = "🛑 **Снять заявки**\n\nВыбери слой (или ВСЕ):"; att = kill_menu()
            elif tl == "/arba":
                reply = (await spreads_text(session_moex)) + "\n\n⚖️ **Выберите связку:**"; att = ARBA_MENU
            elif tl == "/spoof":
                await run_spoof(session_max, user_id); return
            else:
                reply = "Неизвестная команда. Меню:"; att = MAIN_MENU
                
            if reply:
                await send_message(session_max, reply, user_id=tu, chat_id=tc, attachments=att)
    except Exception as e:
        print(f"[ERROR] handle_update: {e}")
        import traceback; traceback.print_exc()

async def main():
    # ДВА коннектора: строгий для MAX, без проверки для публичного MOEX ISS
    ssl_strict = create_ssl_context()
    connector_max = aiohttp.TCPConnector(ssl=ssl_strict)
    connector_moex = aiohttp.TCPConnector(ssl=False)
    
    async with aiohttp.ClientSession(connector=connector_max) as session_max:
        async with aiohttp.ClientSession(connector=connector_moex) as session_moex:
            status, me = await api_get(session_max, "/me")
            print(f"[AUTH] {status}: {'OK' if status == 200 else me}")
            if status != 200: return
            
            asyncio.create_task(monitor_mtlr_spread(session_moex))
            asyncio.create_task(watchdog_bridge(session_max))
            if SPOOF_ENABLED: asyncio.create_task(monitor_spoof(session_max))
            
            print(f"✅ Бот запущен. Торговля DRY/LIVE + kill-switch + watchdog. Live-мигалки: {'ON' if SPOOF_ENABLED else 'OFF'}. MOEX SSL: bypass включён.")
            marker = None
            while True:
                params = {"timeout": 30}
                if marker is not None: params["marker"] = marker
                try: status, data = await api_get(session_max, "/updates", params)
                except Exception as e: print(f"[NET] {e}"); await asyncio.sleep(2); continue
                if status == 401: print("FATAL: токен"); return
                if status != 200: print(f"[ERR] {status}: {data}"); await asyncio.sleep(2); continue
                nm = data.get("marker")
                if nm is not None: marker = nm
                for upd in data.get("updates", []):
                    await handle_update(session_max, session_moex, upd)

if __name__ == "__main__":
    asyncio.run(main())