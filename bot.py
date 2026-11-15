"""
bot.py — ММВБ Агент (v3.8)
Исправлен поиск квартального фьючерса на юань: теперь ищет тикеры вида CRZ6, CRH7 и т.д.
Корректно рассчитывает фандинг на 1 лот (1000 CNY).
"""
import asyncio
import aiohttp
import ssl
import os
from datetime import datetime

BOT_TOKEN = "f9LHodD0cOK58ifY04m7_DqGlAn07KefMTGq0AJQPLp21PgmHPR7qIy9vhMHTAfnQsS4jthZRz_Lg-7sGXgZ"
BASE = "https://platform-api2.max.ru"
HEADERS = {"Authorization": BOT_TOKEN}
MOEX_HEADERS = {"User-Agent": "MMVB-Agent-Bot/3.8"}

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
    "/schedule — расписание торгов"
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
    {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [{"type": "callback", "text": "📊 Котировка", "payload": "cmd_quote"},
                 {"type": "callback", "text": "⚖️ Арбитраж", "payload": "cmd_arba"}],
                [{"type": "callback", "text": "📅 Расписание", "payload": "cmd_schedule"},
                 {"type": "callback", "text": "❓ Справка", "payload": "cmd_help"}]
            ]
        }
    }
]

ARBA_MENU = [
    {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [{"type": "callback", "text": "🔹 MTLR / MTLRP", "payload": "cmd_arba_mtlr"},
                 {"type": "callback", "text": "💱 CNYRUBF / Кварт.", "payload": "cmd_arba_cny"}],
                [{"type": "callback", "text": "✍️ Свои тикеры", "payload": "cmd_arba_manual"},
                 {"type": "callback", "text": "◀️ Назад", "payload": "cmd_back"}]
            ]
        }
    }
]


def fix_layout(text: str) -> str:
    result = []
    for char in text.lower():
        result.append(LAYOUT_MAP.get(char, char))
    return "".join(result).upper().strip()


def create_ssl_context():
    ctx = ssl.create_default_context()
    certs_dir = os.path.join(os.path.dirname(__file__), "certs")
    root_ca = os.path.join(certs_dir, "russian_trusted_root_ca.pem")
    sub_ca = os.path.join(certs_dir, "russian_trusted_sub_ca.pem")
    if os.path.exists(root_ca): ctx.load_verify_locations(root_ca)
    if os.path.exists(sub_ca): ctx.load_verify_locations(sub_ca)
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
    if attachments: payload["attachments"] = attachments
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
        "securities.columns": "SECID,PREVWAPRICE,PREVPRICE,LOT VOLUME,LOTSIZE"
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
                lotsize = sq.get("LOTSIZE") or sq.get("LOT VOLUME") or 1

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
    """Находит ближайший активный квартальный фьючерс на юань (тикер вида CRZ6, CRH7 и т.д.)."""
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json?iss.only=securities&securities.columns=SECID,SECNAME,LASTTRADEDATE"
    try:
        async with session.get(url, headers=MOEX_HEADERS) as resp:
            data = await resp.json()
            rows = data.get("securities", {}).get("data", [])
            cols = data.get("securities", {}).get("columns", [])
            
            active_cny = []
            today = datetime.now().date()
            for row in rows:
                d = dict(zip(cols, row))
                secid = str(d.get("SECID", ""))
                # Ищем тикеры, начинающиеся на CR (кроме вечных типа CRCLF, CRWDF), длина тикера <= 5 (CRZ6)
                if secid.startswith("CR") and len(secid) <= 5 and secid not in ["CRCLF", "CRDOF", "CRWDF", "CRWVF"]:
                    try:
                        ltd_str = d.get("LASTTRADEDATE", "2100-01-01")
                        ltd = datetime.strptime(ltd_str, "%Y-%m-%d").date()
                        if ltd >= today:
                            active_cny.append((secid, ltd, d.get("SECNAME")))
                    except ValueError:
                        continue
            
            if not active_cny:
                return None, "Не найдено активных квартальных фьючерсов на юань."
            
            # Сортируем по дате экспирации, берём ближайший
            active_cny.sort(key=lambda x: x[1])
            nearest = active_cny[0]
            return nearest[0], f"{nearest[0]} (эксп: {nearest[1].strftime('%d.%m.%Y')})"
    except Exception as e:
        print(f"[MOEX ERROR CNY search] {e}")
        return None, f"Ошибка поиска фьючерса: {e}"


async def get_cny_arbitrage(session):
    # 1. Получаем данные вечного фьючерса
    perp_data, perp_text = await get_moex_data(session, "CNYRUBF", "RFUD")
    if perp_data is None:
        return f"⚠️ Не удалось получить данные CNYRUBF.\n{perp_text}"
    
    # 2. Находим ближайший квартальный
    q_ticker, q_name = await get_nearest_cny_futures(session)
    if not q_ticker:
        return f"⚠️ {q_name}"
    
    # 3. Получаем данные квартального
    q_data, q_text = await get_moex_data(session, q_ticker, "RFUD")
    if q_data is None:
        return f"⚠️ Не удалось получить данные {q_ticker}.\n{q_text}"
    
    # 4. Расчёты
    p_price = perp_data["last"] or perp_data["settle"] or perp_data["prev"]
    q_price = q_data["last"] or q_data["settle"] or q_data["prev"]
    lotsize = perp_data.get("lotsize", 1000)
    
    spread = q_price - p_price if (q_price and p_price) else None
    spread_str = f"**{spread:.4f}** ₽" if spread is not None else "Н/Д"
    
    # Фандинг: SWAPRATE_CURR или SWAPRATE. Умножаем на размер лота (1000), чтобы получить сумму на 1 лот.
    swap = perp_data.get("swap_curr") or perp_data.get("swap")
    if swap is not None:
        funding_per_lot = swap * lotsize
        funding_str = f"**{funding_per_lot:.2f} ₽** (ставка: {swap})"
    else:
        funding_str = "Н/Д (нет данных о ставке в API)"

    return (
        f"💱 **Арбитраж: Юань (Вечный vs Квартальный)**\n\n"
        f"🔄 **CNYRUBF** (Вечный, лот={lotsize} CNY)\n"
        f"   Цена: **{p_price}** ₽\n"
        f"   Фандинг на 1 лот: {funding_str}\n\n"
        f"📅 **{q_ticker}** ({q_name})\n"
        f"   Цена: **{q_price}** ₽\n\n"
        f"📏 **Спред** (Кварт. - Вечный): {spread_str}\n"
        f"_Положительный спред означает контанго (квартальный дороже вечного)._"
    )


async def handle_update(session, upd):
    try:
        ut = upd.get("update_type")
        if ut == "message_callback":
            cb = upd.get("callback", {})
            callback_id = cb.get("callback_id")
            payload = cb.get("payload", "")
            user = cb.get("user", {})
            user_id = user.get("user_id")
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
                await send_message(session, "⏳ Загружаю данные по юаню и фандингу...", user_id=user_id)
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
            target_user = user_id if chat_type == "dialog" else None
            target_chat = chat_id if chat_type != "dialog" else None
            print(f"[IN] {chat_type} user={user_id} text={text!r}")
            
            t = text.strip(); t_lower = t.lower()
            reply = None; attachments = None
            state = user_states.get(user_id)
            
            if state and state.get("step") == "AWAITING_TICKER":
                del user_states[user_id]
                if t_lower in ["/help", "/start", "/arba", "/quote"]:
                    reply = "Отмена ввода тикера."; attachments = MAIN_MENU
                else:
                    _, reply = await get_moex_data(session, t); attachments = MAIN_MENU
            elif state and state.get("step") == "AWAITING_ARBA_T1":
                t1 = fix_layout(t)
                user_states[user_id] = {"step": "AWAITING_ARBA_T2", "t1": t1}
                reply = f"Принято: **{t1}**. Теперь введите **второй** тикер:"
            elif state and state.get("step") == "AWAITING_ARBA_T2":
                t2 = fix_layout(t); t1 = state.get("t1", "")
                del user_states[user_id]
                _, r1 = await get_moex_data(session, t1)
                _, r2 = await get_moex_data(session, t2)
                reply = f"⚖️ **{t1} / {t2}**\n\n{r1}\n\n{r2}"; attachments = MAIN_MENU
            elif t_lower in ("/start", "/help"):
                reply = "🏠 **Главное меню:**"; attachments = MAIN_MENU
            elif t_lower == "/quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                reply = "✍️ Введите тикер (SBER, GAZP, MTLR...):"
            elif t_lower == "/arba":
                reply = "⚖️ **Выберите тип арбитража:**"; attachments = ARBA_MENU
            elif t_lower == "/schedule":
                reply = SCHEDULE_TEXT; attachments = MAIN_MENU
            else:
                reply = "Неизвестная команда. Используйте меню ниже:"; attachments = MAIN_MENU
                
            if reply:
                await send_message(session, reply, user_id=target_user, chat_id=target_chat, attachments=attachments)
    except Exception as e:
        print(f"[ERROR] handle_update: {e}")
        import traceback
        traceback.print_exc()


async def main():
    ssl_context = create_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        status, me = await api_get(session, "/me")
        print(f"[AUTH] {status}: {'OK' if status == 200 else me}")
        if status != 200: return
        marker = None
        print("✅ Бот запущен. Жду сообщений...")
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
            new_marker = data.get("marker")
            if new_marker is not None: marker = new_marker
            for upd in data.get("updates", []):
                await handle_update(session, upd)


if __name__ == "__main__":
    asyncio.run(main())