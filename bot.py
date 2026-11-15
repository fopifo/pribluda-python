"""
bot.py — ММВБ Агент (v3.10)
Фоновый алерт: |MTLR - MTLRP| >= 3 руб (раздвижка в обе стороны) -> MAX.
"""
import asyncio
import aiohttp
import ssl
import os
import time
from datetime import datetime

BOT_TOKEN = "f9LHodD0cOK58ifY04m7_DqGlAn07KefMTGq0AJQPLp21PgmHPR7qIy9vhMHTAfnQsS4jthZRz_Lg-7sGXgZ"
BASE = "https://platform-api2.max.ru"
HEADERS = {"Authorization": BOT_TOKEN}
MOEX_HEADERS = {"User-Agent": "MMVB-Agent-Bot/3.10"}

OWNER_ID = 5135149
SPREAD_THRESHOLD = 3.0
POLL_SEC = 30
ALERT_COOLDOWN_SEC = 600

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
    "/quote — узнать котировку\n"
    "/arba — арбитражные связки\n"
    "/schedule — расписание торгов\n\n"
    "Авто-алерт: |MTLR - MTLRP| >= 3 руб (обе стороны)."
)

SCHEDULE_TEXT = (
    "📅 **Расписание торгов ММВБ (МСК)**\n\n"
    "**Будни:**\n"
    "🌅 Утренняя: 06:50 – 09:00\n"
    "☀️ Основная: 09:00 – 19:00\n"
    "🌙 Вечерняя: 19:00 – 23:50\n\n"
    "**ДСВД (сб/вс):** 10:00 – 19:00"
)

MAIN_MENU = [
    {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "📊 Котировка", "payload": "cmd_quote"},
         {"type": "callback", "text": "⚖️ Арбитраж", "payload": "cmd_arba"}],
        [{"type": "callback", "text": "📅 Расписание", "payload": "cmd_schedule"},
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


def fix_layout(text: str) -> str:
    return "".join(LAYOUT_MAP.get(c, c) for c in text.lower()).upper().strip()


def create_ssl_context():
    ctx = ssl.create_default_context()
    certs_dir = os.path.join(os.path.dirname(__file__), "certs")
    for name in ("russian_trusted_root_ca.pem", "russian_trusted_sub_ca.pem"):
        p = os.path.join(certs_dir, name)
        if os.path.exists(p):
            ctx.load_verify_locations(p)
    return ctx


async def api_get(session, path, params=None):
    async with session.get(BASE + path, headers=HEADERS, params=params) as resp:
        try:
            return resp.status, await resp.json()
        except Exception:
            return resp.status, {"raw": await resp.text()}


async def send_message(session, text, user_id=None, chat_id=None, attachments=None):
    params = {"user_id": int(user_id)} if user_id is not None else {"chat_id": int(chat_id)}
    payload = {"text": str(text), "format": "markdown"}
    if attachments:
        payload["attachments"] = attachments
    async with session.post(BASE + "/messages", headers=HEADERS, params=params, json=payload) as resp:
        status = resp.status
        try:
            data = await resp.json()
        except Exception:
            data = {"raw": await resp.text()}
        if status != 200 or data.get("success") is False:
            print(f"[SEND ERROR] status={status} resp={data}")
        return data


async def answer_callback(session, callback_id: str):
    async with session.post(BASE + "/answers", headers=HEADERS, json={"callback_id": str(callback_id)}) as resp:
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
        "securities.columns": "SECID,PREVWAPRICE,PREVPRICE,LOTSIZE"
    }
    try:
        async with session.get(url, params=params, headers=MOEX_HEADERS) as resp:
            if resp.status != 200:
                return None, f"❌ Ошибка запроса к бирже (код {resp.status})"
            data = await resp.json()
            md = data.get("marketdata", {})
            md_rows = md.get("data", [])
            last = low = high = val = swap = swap_curr = settle = None
            if md_rows:
                q = dict(zip(md.get("columns", []), md_rows[0]))
                last = q.get("LAST"); low = q.get("LOW"); high = q.get("HIGH")
                val = q.get("VALTODAY"); swap = q.get("SWAPRATE"); swap_curr = q.get("SWAPRATE_CURR")
                settle = q.get("SETTLEPRICE")
            sec = data.get("securities", {})
            sec_rows = sec.get("data", [])
            prev = lotsize = None
            if sec_rows:
                sq = dict(zip(sec.get("columns", []), sec_rows[0]))
                prev = sq.get("PREVWAPRICE") or sq.get("PREVPRICE")
                lotsize = sq.get("LOTSIZE") or 1
            if last is not None:
                change_abs = last - prev if prev else 0
                change_pct = (change_abs / prev * 100) if prev else 0
                arrow = "🟢" if change_abs >= 0 else "🔴"
                note = ""
                price_str = f"**{last}** ₽"
            elif prev is not None:
                price_str = f"**{prev}** ₽"
                note = "\n_(⚠️ Сессия закрыта. Цена закрытия пред. дня)_"
                arrow = ""; change_pct = 0
            else:
                return None, f"⚠️ По тикеру **{ticker}** нет данных."
            text = (
                f"📊 **{ticker}** ({board})\n\n"
                f"💰 Цена: {price_str}  {arrow} {change_pct:+.2f}%{note}\n"
                f"📉 Мин: {low if low is not None else 'Н/Д'} ₽  |  📈 Макс: {high if high is not None else 'Н/Д'} ₽\n"
                f"💵 Оборот: {fmt_val(val)}"
            )
            return {"last": last, "prev": prev, "swap": swap, "swap_curr": swap_curr, "settle": settle, "lotsize": lotsize}, text
    except Exception as e:
        print(f"[MOEX ERROR] {e}")
        return None, f"❌ Ошибка при запросе к бирже: {e}"


async def get_nearest_cny_futures(session):
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json?iss.only=securities&securities.columns=SECID,SECNAME,LASTTRADEDATE"
    try:
        async with session.get(url, headers=MOEX_HEADERS) as resp:
            data = await resp.json()
            rows = data.get("securities", {}).get("data", [])
            cols = data.get("securities", {}).get("columns", [])
            active = []
            today = datetime.now().date()
            for row in rows:
                d = dict(zip(cols, row))
                secid = str(d.get("SECID", ""))
                if secid.startswith("CR") and len(secid) <= 5 and secid not in ["CRCLF", "CRDOF", "CRWDF", "CRWVF"]:
                    try:
                        ltd = datetime.strptime(d.get("LASTTRADEDATE", "2100-01-01"), "%Y-%m-%d").date()
                        if ltd >= today:
                            active.append((secid, ltd, d.get("SECNAME")))
                    except ValueError:
                        continue
            if not active:
                return None, "Не найдено активных квартальных фьючерсов на юань."
            active.sort(key=lambda x: x[1])
            n = active[0]
            return n[0], f"{n[0]} (эксп: {n[1].strftime('%d.%m.%Y')})"
    except Exception as e:
        return None, f"Ошибка поиска фьючерса: {e}"


async def get_cny_arbitrage(session):
    perp, pt = await get_moex_data(session, "CNYRUBF", "RFUD")
    if perp is None:
        return f"⚠️ Не удалось получить CNYRUBF.\n{pt}"
    qt, qn = await get_nearest_cny_futures(session)
    if not qt:
        return f"⚠️ {qn}"
    qd, qt2 = await get_moex_data(session, qt, "RFUD")
    if qd is None:
        return f"⚠️ Не удалось получить {qt}.\n{qt2}"
    p = perp["last"] or perp["settle"] or perp["prev"]
    q = qd["last"] or qd["settle"] or qd["prev"]
    lot = perp.get("lotsize", 1000)
    spread = q - p if (q and p) else None
    spread_str = f"**{spread:.4f}** ₽" if spread is not None else "Н/Д"
    swap = perp.get("swap_curr") or perp.get("swap")
    funding = f"**{swap * lot:.2f} ₽** (ставка {swap})" if swap is not None else "Н/Д"
    return (
        f"💱 **Юань: вечный vs квартальный**\n\n"
        f"🔄 CNYRUBF (лот={lot}): **{p}** ₽\n   Фандинг/лот: {funding}\n\n"
        f"📅 {qt} ({qn}): **{q}** ₽\n\n"
        f"📏 Спред (кварт-вечн): {spread_str}"
    )


async def monitor_mtlr_spread(session):
    """Фоновый алерт: |MTLR - MTLRP| >= SPREAD_THRESHOLD (обе стороны)."""
    prev_mag = None
    last_alert = 0.0
    while True:
        try:
            d1, _ = await get_moex_data(session, "MTLR", "TQBR")
            d2, _ = await get_moex_data(session, "MTLRP", "TQBR")
            if d1 and d2:
                p1 = d1["last"] or d1["prev"]
                p2 = d2["last"] or d2["prev"]
                if p1 is not None and p2 is not None:
                    spread = p1 - p2
                    mag = abs(spread)
                    now = time.time()
                    cross = prev_mag is None or prev_mag < SPREAD_THRESHOLD
                    if mag >= SPREAD_THRESHOLD and (cross or now - last_alert > ALERT_COOLDOWN_SEC):
                        direction = "MTLR дороже MTLRP" if spread > 0 else "MTLRP дороже MTLR"
                        msg = (
                            f"🚨 **Раздвижка Мечел >= {SPREAD_THRESHOLD:.0f}₽**\n\n"
                            f"MTLR: **{p1}** ₽\nMTLRP: **{p2}** ₽\n"
                            f"Спред: **{spread:+.2f}** ₽ (|{mag:.2f}|)\n"
                            f"{direction}"
                        )
                        print(f"[ALERT] MTLR spread={spread:+.2f} |{mag:.2f}|")
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
            elif payload == "cmd_arba":
                await send_message(session, "⚖️ **Выберите тип арбитража:**", user_id=user_id, attachments=ARBA_MENU)
            elif payload == "cmd_arba_mtlr":
                await send_message(session, "⏳ Считаю MTLR / MTLRP...", user_id=user_id)
                _, t1 = await get_moex_data(session, "MTLR", "TQBR")
                _, t2 = await get_moex_data(session, "MTLRP", "TQBR")
                await send_message(session, f"⚖️ **MTLR / MTLRP**\n\n{t1}\n\n{t2}", user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_cny":
                await send_message(session, "⏳ Загружаю юань...", user_id=user_id)
                reply = await get_cny_arbitrage(session)
                await send_message(session, reply, user_id=user_id, attachments=MAIN_MENU)
            elif payload == "cmd_arba_manual":
                user_states[user_id] = {"step": "AWAITING_ARBA_T1"}
                await send_message(session, "✍️ Введите **первый** тикер:", user_id=user_id)
            elif payload == "cmd_schedule":
                await send_message(session, SCHEDULE_TEXT, user_id=user_id, attachments=MAIN_MENU)
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
                if tl in ["/help", "/start", "/arba", "/quote"]:
                    reply = "Отмена ввода."; att = MAIN_MENU
                else:
                    _, reply = await get_moex_data(session, t); att = MAIN_MENU
            elif st and st.get("step") == "AWAITING_ARBA_T1":
                user_states[user_id] = {"step": "AWAITING_ARBA_T2", "t1": fix_layout(t)}
                reply = f"Принято: **{fix_layout(t)}**. Второй тикер:"
            elif st and st.get("step") == "AWAITING_ARBA_T2":
                t2 = fix_layout(t); t1 = st.get("t1", "")
                del user_states[user_id]
                _, r1 = await get_moex_data(session, t1)
                _, r2 = await get_moex_data(session, t2)
                reply = f"⚖️ **{t1} / {t2}**\n\n{r1}\n\n{r2}"; att = MAIN_MENU
            elif tl in ("/start", "/help"):
                reply = "🏠 **Главное меню:**"; att = MAIN_MENU
            elif tl == "/quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                reply = "✍️ Введите тикер:"
            elif tl == "/arba":
                reply = "⚖️ **Выберите тип арбитража:**"; att = ARBA_MENU
            elif tl == "/schedule":
                reply = SCHEDULE_TEXT; att = MAIN_MENU
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
        print(f"✅ Бот запущен. Мониторинг |MTLR-MTLRP| >= {SPREAD_THRESHOLD:.0f}₽ включён.")
        marker = None
        while True:
            params = {"timeout": 30}
            if marker is not None: params["marker"] = marker
            try:
                status, data = await api_get(session, "/updates", params)
            except Exception as e:
                print(f"[NET] {e}"); await asyncio.sleep(2); continue
            if status == 401:
                print("FATAL: токен недействителен"); return
            if status != 200:
                print(f"[ERR] {status}: {data}"); await asyncio.sleep(2); continue
            nm = data.get("marker")
            if nm is not None: marker = nm
            for upd in data.get("updates", []):
                await handle_update(session, upd)


if __name__ == "__main__":
    asyncio.run(main())