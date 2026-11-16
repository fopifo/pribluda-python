"""
bot.py — ММВБ Агент (v3.21)
- Торговля (DRY-RUN): тестовый режим — ТОЛЬКО MTLR x 1 лот.
  Кнопка 🛒 Торговля → Купить/Продать → превью → подтверждение → dry-run POST на мост.
- Баланс (оценка моста за сегодня, с пометкой про lite-invest).
- Мигалки со стрелками; Арбитраж спреды; Позиции группами; алерт |MTLR-MTLRP|>=3.
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
MOEX_HEADERS = {"User-Agent": "MMVB-Agent-Bot/3.21"}
BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

BRIDGE_URL = "http://127.0.0.1:8787"
BRIDGE_TOKEN = "pribluda-local-1"
TEST_SYMBOL = "MTLR"
TEST_QTY = 1

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
    "/spoof — мигалки (роботы в стакане)\n"
    "/trade — торговля (dry-run, только MTLR x 1)\n\n"
    "Авто-алерт: |MTLR - MTLRP| >= 3 руб."
)

MAIN_MENU = [
    {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "📊 Котировка", "payload": "cmd_quote"},
         {"type": "callback", "text": "⚖️ Арбитраж", "payload": "cmd_arba"}],
        [{"type": "callback", "text": "📌 Позиции", "payload": "cmd_positions"},
         {"type": "callback", "text": "💰 Баланс", "payload": "cmd_balance"}],
        [{"type": "callback", "text": "🛒 Торговля", "payload": "cmd_trade"},
         {"type": "callback", "text": "🕵️ Мигалки", "payload": "cmd_spoof"}],
        [{"type": "callback", "text": "❓ Справка", "payload": "cmd_help"}]
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
            ctx.load_verify_locations(p)
    return ctx


def read_bridge_state():
    p = DATA_DIR / "bridge_state.json"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def format_positions(state):
    if not state:
        return "⚠️ Мост не отдаёт состояние."
    syms = state.get("symbols", {})
    open_syms = {s: i for s, i in syms.items() if i.get("position", 0) != 0}
    age = (time.time() * 1000 - state.get("updated_ms", 0)) / 1000
    if not open_syms:
        return f"📌 Открытых позиций нет. (обновлено {age:.0f}с)"

    def leg(s):
        i = open_syms[s]
        p = i["position"]
        side = "лонг" if p > 0 else "шорт"
        return f"   {s}: {side} {abs(p):g} @ {i.get('pos_price', 0):g}"

    def mid(s):
        i = syms.get(s, {})
        bb = i.get("best_bid", 0); ba = i.get("best_ask", 0)
        return (bb + ba) / 2 if bb > 0 and ba > 0 else None

    lines = [f"📌 **Открытые позиции** (обновлено {age:.0f}с)\n"]
    mech = [s for s in ("MTLR", "MTLRP") if s in open_syms]
    if mech:
        lines.append("🔹 **Мечел**")
        lines += [leg(s) for s in mech]
        a, b = mid("MTLR"), mid("MTLRP")
        if a and b:
            lines.append(f"   спред MTLR−MTLRP: {a - b:+.2f}")
        lines.append("")
    cny = [s for s in open_syms if s == "CNYRUBF" or s.startswith("CNY-")]
    if cny:
        lines.append("💱 **Юань**")
        lines += [leg(s) for s in sorted(cny)]
        perp = mid("CNYRUBF")
        q = [mid(s) for s in sorted(cny) if s.startswith("CNY-")]
        if perp and q and q[0]:
            lines.append(f"   спред кварт−вечн: {q[0] - perp:+.4f}")
        lines.append("")
    for s in sorted([s for s in open_syms if s not in mech and s not in cny]):
        lines.append(leg(s).strip())
    return "\n".join(lines).rstrip()


async def format_balance(session, state):
    if not state:
        return "⚠️ Мост не отдаёт состояние."
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
    lines = ["🕵️ **Мигалки — роботы в стакане**", ""]
    cands = data.get("candidates", [])
    if not cands:
        lines.append("Сейчас явных роботов-«мигалок» не видно.")
        lines.append("")
    else:
        for c in cands[:4]:
            if c["side"] == "bids":
                head = f"🟢⬆️ **{c['sym']}** — ПОДДЕРЖКА (крупные покупки) по {c['price']}"
            else:
                head = f"🔴️ **{c['sym']}** — СОПРОТИВЛЕНИЕ (крупные продажи) по {c['price']}"
            lines.append(head)
            lines.append(f"   Повторяет каждые ~{human_sec(c['period_s'])}; замечено циклов: {c['n']}, регулярность {c['reg']:.0%}.")
            lines.append(f"   Держит заявку ~{human_sec(c['life_s'])}. {TAG_RU.get(c['tag'], c['tag'])}.")
            lines.append("")
    act = data.get("activity", {})
    if act:
        top = sorted(act.items(), key=lambda kv: -kv[1]["rate"])[:3]
        lines.append("📈 Активность рынка: " + "; ".join(f"{s} {v['rate']}/с" for s, v in top))
    lines.append("\n_🟢⬆️ поддержка = цена скорее отскочит ВВЕРХ; 🔴⬇️ сопротивление = скорее ВНИЗ._")
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


async def get_moex_data(session, ticker: str, board: str = "TQBR"):
    engine = "stock" if board in ["TQBR", "CETS"] else "futures"
    market = "shares" if board == "TQBR" else ("selt" if board == "CETS" else "forts")
    url = f"https://iss.moex.com/iss/engines/{engine}/markets/{market}/boards/{board}/securities/{ticker}.json"
    params = {
        "iss.only": "marketdata,securities",
        "marketdata.columns": "SECID,LAST,LOW,HIGH,VALTODAY,SWAPRATE,SWAPRATE_CURR,SETTLEPRICE",
        "securities.columns": "SECID,PREVWAPRICE,PREVPRICE,PREVSETTLE,LOTSIZE"
    }
    try:
        async with session.get(url, params=params, headers=MOEX_HEADERS) as resp:
            if resp.status != 200:
                return None, f"❌ Ошибка биржи ({resp.status})"
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
            text = (
                f"📊 **{ticker}** ({board})\n\n"
                f"💰 Цена: {price_str}  {arrow} {change_pct:+.2f}%{note}\n"
                f"📉 Мин: {low if low is not None else 'Н/Д'} ₽  |  📈 Макс: {high if high is not None else 'Н/Д'} ₽\n"
                f"💵 Оборот: {fmt_val(val)}"
            )
            return {"last": last, "prev": prev, "prev_settle": prev_settle, "swap": swap,
                    "swap_curr": swap_curr, "settle": settle, "lotsize": lotsize}, text
    except Exception as e:
        print(f"[MOEX ERROR] {e}")
        return None, f"❌ Ошибка: {e}"


async def get_price(session, ticker, board="TQBR"):
    d, _ = await get_moex_data(session, ticker, board)
    return (d["last"] or d["prev"]) if d else None


async def get_nearest_cny_futures(session):
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json?iss.only=securities&securities.columns=SECID,SECNAME,LASTTRADEDATE"
    try:
        async with session.get(url, headers=MOEX_HEADERS) as resp:
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


async def spreads_text(session):
    m1 = await get_price(session, "MTLR"); m2 = await get_price(session, "MTLRP")
    cny = await get_price(session, "CNYRUBF", "RFUD")
    qt, _ = await get_nearest_cny_futures(session)
    q = await get_price(session, qt, "RFUD") if qt else None
    lines = ["📐 **Спреды сейчас:**"]
    if m1 is not None and m2 is not None:
        lines.append(f"🔹 Мечел MTLR−MTLRP: **{m1 - m2:+.2f} ₽**")
    if q is not None and cny is not None:
        lines.append(f"💱 Юань кварт−вечн: **{q - cny:+.4f} ₽**")
    return "\n".join(lines)


async def get_cny_arbitrage(session):
    perp, pt = await get_moex_data(session, "CNYRUBF", "RFUD")
    if perp is None: return f"⚠️ Не удалось получить CNYRUBF.\n{pt}"
    qt, qn = await get_nearest_cny_futures(session)
    if not qt: return f"⚠️ {qn}"
    qd, qt2 = await get_moex_data(session, qt, "RFUD")
    if qd is None: return f"⚠️ Не удалось получить {qt}.\n{qt2}"
    p = perp["last"] or perp["settle"] or perp["prev"]
    q = qd["last"] or qd["settle"] or qd["prev"]
    lot = perp.get("lotsize", 1000)
    spread = q - p if (q and p) else None
    spread_str = f"**{spread:.4f} ₽**" if spread is not None else "Н/Д"
    swap = perp.get("swap_curr") or perp.get("swap")
    funding = f"**{swap * lot:.2f} ₽** (ставка {swap})" if swap is not None else "Н/Д"
    return (
        f"💱 **Юань: вечный vs квартальный**\n\n"
        f"🔄 CNYRUBF (лот={lot}): **{p}** ₽\n   Фандинг/лот: {funding}\n\n"
        f"📅 {qt} ({qn}): **{q}** ₽\n\n"
        f"📏 Спред (кварт−вечн): {spread_str}"
    )


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


async def bridge_post(session, path, body, timeout=5):
    headers = {"Content-Type": "application/json"}
    try:
        async with session.post(BRIDGE_URL + path, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return resp.status, await resp.json()
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}


def trade_menu():
    return [
        {"type": "inline_keyboard", "payload": {"buttons": [
            [{"type": "callback", "text": "🟢 Купить MTLR x 1", "payload": "trade_preview:buy"}],
            [{"type": "callback", "text": "🔴 Продать MTLR x 1", "payload": "trade_preview:sell"}],
            [{"type": "callback", "text": "◀️ В главное меню", "payload": "cmd_back"}]
        ]}}
    ]


def confirm_menu(side):
    label = "купить" if side == "buy" else "продать"
    return [
        {"type": "inline_keyboard", "payload": {"buttons": [
            [{"type": "callback", "text": f"✅ Подтвердить {label}", "payload": f"trade_confirm:{side}"}],
            [{"type": "callback", "text": "❌ Отмена", "payload": "cmd_back"}]
        ]}}
    ]


def trade_preview_text(side):
    state = read_bridge_state()
    if not state:
        return "⚠️ Мост не отдаёт состояние."
    info = state.get("symbols", {}).get(TEST_SYMBOL)
    if not info:
        return f"⚠️ Нет данных по {TEST_SYMBOL}."
    if not info.get("ready"):
        return f"⚠️ Слой {TEST_SYMBOL} не готов (нет стакана)."
    bb = info.get("best_bid", 0); ba = info.get("best_ask", 0)
    price = ba if side == "buy" else bb
    if price <= 0:
        return f"⚠️ Нет цены {'ask' if side == 'buy' else 'bid'} по {TEST_SYMBOL}."
    side_ru = "ПОКУПКА" if side == "buy" else "ПРОДАЖА"
    emoji = "🟢" if side == "buy" else "🔴"
    return (
        f"{emoji} **Торговля — DRY-RUN** (заявка НЕ уйдёт на биржу)\n\n"
        f"**{side_ru}: {TEST_SYMBOL} × {TEST_QTY} лот**\n"
        f"Цена (best {'ask' if side == 'buy' else 'bid'}): **{price:g} ₽**\n"
        f"Сумма: **{price * TEST_QTY:,.2f} ₽**\n\n"
        f"🔸 _Тестовый режим: разрешён только {TEST_SYMBOL} x {TEST_QTY}._\n"
        f"🔸 _Подтверждение — ниже._"
    )


async def do_trade_confirm(session, side, user_id):
    state = read_bridge_state()
    if not state:
        await send_message(session, "⚠️ Мост не отдаёт состояние.", user_id=user_id, attachments=MAIN_MENU); return
    info = state.get("symbols", {}).get(TEST_SYMBOL)
    if not info or not info.get("ready"):
        await send_message(session, f"⚠️ Слой {TEST_SYMBOL} не готов.", user_id=user_id, attachments=MAIN_MENU); return
    bb = info.get("best_bid", 0); ba = info.get("best_ask", 0)
    price = ba if side == "buy" else bb
    if price <= 0:
        await send_message(session, f"⚠️ Нет цены по {TEST_SYMBOL}.", user_id=user_id, attachments=MAIN_MENU); return
    body = {
        "token": BRIDGE_TOKEN,
        "id": f"bot-{uuid.uuid4().hex}",
        "symbol": TEST_SYMBOL,
        "side": side,
        "qty": TEST_QTY,
        "type": "limit",
        "price": price,
    }
    status, data = await bridge_post(session, "/order", body)
    if status != 200 or not data.get("ok"):
        err = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
        await send_message(session, f"❌ Мост отклонил: {err}", user_id=user_id, attachments=MAIN_MENU); return
    if data.get("duplicate"):
        await send_message(session, "⚠️ Команда уже была отправлена (дубль).", user_id=user_id, attachments=MAIN_MENU); return
    side_ru = "ПОКУПКА" if side == "buy" else "ПРОДАЖА"
    emoji = "🟢" if side == "buy" else "🔴"
    await send_message(session,
        f"{emoji} **DRY-RUN: команда записана в лог**\n\n"
        f"{side_ru}: {data.get('symbol')} × {data.get('qty')} @ {data.get('price')} ₽\n\n"
        f"🔸 Заявка **НЕ** отправлена на биржу.\n"
        f"🔸 Запись в `data/trade_commands.jsonl`.",
        user_id=user_id, attachments=MAIN_MENU)


async def monitor_mtlr_spread(session):
    prev_mag = None
    last_alert = 0.0
    while True:
        try:
            d1, _ = await get_moex_data(session, "MTLR", "TQBR")
            d2, _ = await get_moex_data(session, "MTLRP", "TQBR")
            if d1 and d2:
                p1 = d1["last"] or d1["prev"]; p2 = d2["last"] or d2["prev"]
                if p1 is not None and p2 is not None:
                    spread = p1 - p2; mag = abs(spread); now = time.time()
                    cross = prev_mag is None or prev_mag < SPREAD_THRESHOLD
                    if mag >= SPREAD_THRESHOLD and (cross or now - last_alert > ALERT_COOLDOWN_SEC):
                        direction = "MTLR дороже MTLRP" if spread > 0 else "MTLRP дороже MTLR"
                        msg = (
                            f"🚨 **Раздвижка Мечел >= {SPREAD_THRESHOLD:.0f}₽**\n\n"
                            f"MTLR: **{p1}** ₽\nMTLRP: **{p2}** ₽\n"
                            f"Спред: **{spread:+.2f}** ₽ (|{mag:.2f}|)\n"
                            f"{direction}"
                        )
                        await send_message(session, msg, user_id=OWNER_ID)
                        last_alert = now
                    prev_mag = mag
        except Exception as e:
            print(f"[MONITOR ERROR] {e}")
        await asyncio.sleep(POLL_SEC)


async def handle_update(session, upd):
    try:
        ut = upd.get("update_type")
        if ut == "message_callback":
            cb = upd.get("callback", {})
            callback_id = cb.get("callback_id")
            payload = cb.get("payload", "")
            user_id = (cb.get("user") or {}).get("user_id")
            print(f"[IN] callback payload={payload!r}")
            await answer_callback(session, callback_id)
            if payload == "cmd_quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                await send_message(session, "✍️ Введите тикер (SBER, GAZP, MTLR...):", user_id=user_id)
            elif payload == "cmd_positions":
                await send_message(session, format_positions(read_bridge_state()), user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_balance":
                await send_message(session, await format_balance(session, read_bridge_state()), user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_spoof":
                await run_spoof(session, user_id)
            elif payload == "cmd_trade":
                await send_message(session, "🛒 **Торговля (DRY-RUN)**\n\nТестовый режим: только MTLR × 1 лот.\n\nВыберите действие:", user_id=user_id, attachments=trade_menu())
            elif payload == "trade_preview:buy":
                await send_message(session, trade_preview_text("buy"), user_id=user_id, attachments=confirm_menu("buy"))
            elif payload == "trade_preview:sell":
                await send_message(session, trade_preview_text("sell"), user_id=user_id, attachments=confirm_menu("sell"))
            elif payload.startswith("trade_confirm:"):
                side = payload.split(":", 1)[1]
                if side in ("buy", "sell"):
                    await do_trade_confirm(session, side, user_id)
                else:
                    await send_message(session, "⚠️ Неизвестная сторона.", user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba":
                txt = await spreads_text(session)
                await send_message(session, txt + "\n\n⚖️ **Выберите связку:**", user_id=user_id, attachments=ARBA_MENU)
            elif payload == "cmd_arba_mtlr":
                await send_message(session, "⏳ Считаю MTLR / MTLRP...", user_id=user_id)
                d1, t1 = await get_moex_data(session, "MTLR", "TQBR")
                d2, t2 = await get_moex_data(session, "MTLRP", "TQBR")
                sp = ""
                if d1 and d2:
                    a = d1["last"] or d1["prev"]; b = d2["last"] or d2["prev"]
                    if a is not None and b is not None:
                        sp = f"\n\n📐 **Спред MTLR−MTLRP: {a - b:+.2f} ₽**"
                await send_message(session, f"⚖️ **MTLR / MTLRP**{sp}\n\n{t1}\n\n{t2}", user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_cny":
                await send_message(session, "⏳ Загружаю юань...", user_id=user_id)
                reply = await get_cny_arbitrage(session)
                await send_message(session, reply, user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_manual":
                user_states[user_id] = {"step": "AWAITING_ARBA_T1"}
                await send_message(session, "✍️ Введите **первый** тикер:", user_id=user_id)
            elif payload == "cmd_help":
                await send_message(session, HELP_TEXT, user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_back":
                await send_message(session, "🏠 **Главное меню:**", user_id=user_id, attachments=MAIN_MENU)
            return
        if ut == "message_created":
            msg = upd.get("message") or {}
            sender = msg.get("sender") or {}
            recipient = msg.get("recipient") or {}
            if sender.get("is_bot"): return
            text = (msg.get("body") or {}).get("text") or ""
            user_id = sender.get("user_id")
            chat_id = recipient.get("chat_id")
            chat_type = recipient.get("chat_type")
            tu = user_id if chat_type == "dialog" else None
            tc = chat_id if chat_type != "dialog" else None
            print(f"[IN] {chat_type} user={user_id} text={text!r}")
            t = text.strip(); tl = t.lower()
            reply = None; att = None
            st = user_states.get(user_id)
            if st and st.get("step") == "AWAITING_TICKER":
                del user_states[user_id]
                if tl in ["/help", "/start", "/arba", "/quote", "/positions", "/spoof", "/balance", "/trade"]:
                    reply = "Отмена ввода."; att = MAIN_MENU
                else:
                    _, reply = await get_moex_data(session, t); att = MAIN_MENU
            elif st and st.get("step") == "AWAITING_ARBA_T1":
                user_states[user_id] = {"step": "AWAITING_ARBA_T2", "t1": fix_layout(t)}
                reply = f"Принято: **{fix_layout(t)}**. Второй тикер:"
            elif st and st.get("step") == "AWAITING_ARBA_T2":
                t2 = fix_layout(t); t1 = st.get("t1", "")
                del user_states[user_id]
                d1, r1 = await get_moex_data(session, t1)
                d2, r2 = await get_moex_data(session, t2)
                sp = ""
                if d1 and d2:
                    a = d1["last"] or d1["prev"]; b = d2["last"] or d2["prev"]
                    if a is not None and b is not None:
                        sp = f"\n\n📐 **Спред {t1}−{t2}: {a - b:+.4f} ₽**"
                reply = f"⚖️ **{t1} / {t2}**{sp}\n\n{r1}\n\n{r2}"; att = MAIN_MENU
            elif tl in ("/start", "/help"):
                reply = "🏠 **Главное меню:**"; att = MAIN_MENU
            elif tl == "/quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                reply = "✍️ Введите тикер:"
            elif tl == "/positions":
                reply = format_positions(read_bridge_state()); att = MAIN_MENU
            elif tl == "/balance":
                reply = await format_balance(session, read_bridge_state()); att = MAIN_MENU
            elif tl == "/trade":
                reply = "🛒 **Торговля (DRY-RUN)**\n\nТолько MTLR × 1 лот.\n\nВыберите действие:"; att = trade_menu()
            elif tl == "/arba":
                reply = (await spreads_text(session)) + "\n\n⚖️ **Выберите связку:**"; att = ARBA_MENU
            elif tl == "/spoof":
                await run_spoof(session, user_id); return
            else:
                reply = "Неизвестная команда. Меню:"; att = MAIN_MENU
            if reply:
                await send_message(session, reply, user_id=tu, chat_id=tc, attachments=att)
    except Exception as e:
        print(f"[ERROR] handle_update: {e}")
        import traceback; traceback.print_exc()


async def main():
    ssl_context = create_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        status, me = await api_get(session, "/me")
        print(f"[AUTH] {status}: {'OK' if status == 200 else me}")
        if status != 200: return
        asyncio.create_task(monitor_mtlr_spread(session))
        print(f"✅ Бот запущен. Торговля (DRY-RUN, только {TEST_SYMBOL} x {TEST_QTY}) + позиции + спреды + мигалки.")
        marker = None
        while True:
            params = {"timeout": 30}
            if marker is not None: params["marker"] = marker
            try: status, data = await api_get(session, "/updates", params)
            except Exception as e: print(f"[NET] {e}"); await asyncio.sleep(2); continue
            if status == 401: print("FATAL: токен"); return
            if status != 200: print(f"[ERR] {status}: {data}"); await asyncio.sleep(2); continue
            nm = data.get("marker")
            if nm is not None: marker = nm
            for upd in data.get("updates", []):
                await handle_update(session, upd)


if __name__ == "__main__":
    asyncio.run(main())