"""
bot.py — ММВБ Агент (v3.3)
Критическое исправление: убран ненадежный endpoint свечей MOEX (отдавал данные за 2011 год для MTLR).
Теперь цены и дельта берутся строго из marketdata (LAST) и securities (PREVPRICE).
"""
import asyncio
import aiohttp
import ssl
import os

BOT_TOKEN = "f9LHodD0cOK58ifY04m7_DqGlAn07KefMTGq0AJQPLp21PgmHPR7qIy9vhMHTAfnQsS4jthZRz_Lg-7sGXgZ"
BASE = "https://platform-api2.max.ru"
HEADERS = {"Authorization": BOT_TOKEN}
MOEX_HEADERS = {"User-Agent": "MMVB-Agent-Bot/3.3"}

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
    "/start — начать\n"
    "/help — эта справка\n"
    "/quote — узнать котировку (бот попросит ввести тикер)\n"
    "/arba — меню арбитражных связок"
)


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
        except Exception as e:
            return resp.status, {"raw_error": str(e), "text": await resp.text()}


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
    async with session.post(BASE + "/answers", headers=HEADERS,
                            json={"callback_id": str(callback_id)}) as resp:
        return await resp.json()


def fmt_val(v):
    if v is None: return "Н/Д"
    if v >= 1_000_000_000: return f"{v / 1_000_000_000:.2f} млрд ₽"
    if v >= 1_000_000: return f"{v / 1_000_000:.2f} млн ₽"
    return f"{v:,.0f} ₽"


async def get_moex_data(session, ticker: str):
    """Единый надежный запрос к MOEX без использования сломанных свечей."""
    t = fix_layout(ticker)
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{t}.json"
    params = {
        "iss.only": "marketdata,securities",
        "marketdata.columns": "SECID,LAST,LOW,HIGH,VALTODAY",
        "securities.columns": "SECID,PREVWAPRICE,PREVPRICE"
    }
    try:
        async with session.get(url, params=params, headers=MOEX_HEADERS) as resp:
            if resp.status != 200:
                return None, f"❌ Ошибка запроса к бирже (код {resp.status})"
            data = await resp.json()

            md = data.get("marketdata", {})
            md_rows = md.get("data", [])
            last = low = high = val = None
            
            if md_rows:
                q = dict(zip(md.get("columns", []), md_rows[0]))
                last = q.get("LAST")
                low = q.get("LOW")
                high = q.get("HIGH")
                val = q.get("VALTODAY")

            prev = None
            sec = data.get("securities", {})
            sec_rows = sec.get("data", [])
            if sec_rows:
                sq = dict(zip(sec.get("columns", []), sec_rows[0]))
                prev = sq.get("PREVWAPRICE") or sq.get("PREVPRICE")

            if last is not None:
                change_abs = last - prev if prev else 0
                change_pct = (change_abs / prev * 100) if prev else 0
                arrow = "🟢" if change_abs >= 0 else "🔴"
                note = ""
                price_str = f"**{last}** ₽"
            elif prev is not None:
                price_str = f"**{prev}** ₽"
                note = "\n_(⚠️ Сессия закрыта. Показана цена закрытия предыдущего дня)_"
                change_abs = 0
                change_pct = 0
                arrow = ""
            else:
                return None, f"⚠️ По тикеру **{t}** нет данных (режим TQBR)."

            text = (
                f"📊 **{t}** (Московская Биржа)\n\n"
                f"💰 Цена: {price_str}  {arrow} {change_pct:+.2f}% за день{note}\n"
                f"📉 Мин: {low if low is not None else 'Н/Д'} ₽  |  📈 Макс: {high if high is not None else 'Н/Д'} ₽\n"
                f"💵 Оборот: {fmt_val(val)}"
            )
            return last, text

    except Exception as e:
        print(f"[MOEX ERROR] {e}")
        return None, f"❌ Ошибка при запросе к бирже: {e}"


async def get_arbitrage_data(session, t1: str, t2: str) -> str:
    p1, msg1 = await get_moex_data(session, t1)
    p2, msg2 = await get_moex_data(session, t2)
    
    if p1 is None or p2 is None:
        # Если одна из ошибок не критична, покажем её, но лучше вернуть общий текст
        return f"⚠️ Не удалось получить данные.\n{msg1 if p1 is None else ''}\n{msg2 if p2 is None else ''}"
    
    # Извлекаем процент изменения из сообщений (грубо, но надежно) или пересчитаем
    # Для чистоты пересчитаем спред
    spread = p1 - p2
    ratio = p1 / p2 if p2 else 0
    
    return (
        f"⚖️ **Арбитраж: {fix_layout(t1)} / {fix_layout(t2)}**\n\n"
        f"🔹 **{fix_layout(t1)}**: {p1} ₽\n"
        f"🔸 **{fix_layout(t2)}**: {p2} ₽\n\n"
        f"📏 Спред: **{spread:.2f}** ₽\n"
        f"📊 Отношение: {fix_layout(t1)}/{fix_layout(t2)} = **{ratio:.4f}**"
    )


ARBA_KEYBOARD = [
    {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [
                    {"type": "callback", "text": "🔹 Двуногий (MTLR/MTLRP)", "payload": "cmd_arba_2"},
                    {"type": "callback", "text": "🔸 Трёхногий", "payload": "cmd_arba_3"}
                ],
                [
                    {"type": "callback", "text": "✍️ Ввести свои тикеры", "payload": "cmd_arba_manual"}
                ]
            ]
        }
    }
]


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

            if payload == "cmd_arba_2":
                await send_message(session, "⏳ Считаю MTLR / MTLRP...", user_id=user_id)
                reply = await get_arbitrage_data(session, "MTLR", "MTLRP")
                await send_message(session, reply, user_id=user_id)
            elif payload == "cmd_arba_3":
                await send_message(session, "🚧 Трёхногий арбитраж в разработке.", user_id=user_id)
            elif payload == "cmd_arba_manual":
                user_states[user_id] = {"step": "AWAITING_ARBA_T1"}
                await send_message(session, "✍️ Введите **первый** тикер:", user_id=user_id)
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

            t = text.strip()
            t_lower = t.lower()
            reply = None
            attachments = None
            state = user_states.get(user_id)

            if state and state.get("step") == "AWAITING_TICKER":
                del user_states[user_id]
                if t_lower in ["/help", "/start", "/arba", "/quote"]:
                    reply = "Отмена ввода тикера."
                else:
                    _, reply = await get_moex_data(session, t)

            elif state and state.get("step") == "AWAITING_ARBA_T1":
                t1 = fix_layout(t)
                user_states[user_id] = {"step": "AWAITING_ARBA_T2", "t1": t1}
                reply = f"Принято: **{t1}**. Теперь введите **второй** тикер:"

            elif state and state.get("step") == "AWAITING_ARBA_T2":
                t2 = fix_layout(t)
                t1 = state.get("t1", "")
                del user_states[user_id]
                reply = await get_arbitrage_data(session, t1, t2)

            elif t_lower in ("/start", "/help"):
                reply = HELP_TEXT
            elif t_lower == "/quote":
                user_states[user_id] = {"step": "AWAITING_TICKER"}
                reply = "✍️ Введите тикер (SBER, GAZP, MTLR...).\n_Любая раскладка и регистр._"
            elif t_lower == "/arba":
                reply = "⚖️ **Выберите тип арбитражной связки:**"
                attachments = ARBA_KEYBOARD
            else:
                reply = "Неизвестная команда. Напишите /help"

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
                print(f"[NET] {e}")
                await asyncio.sleep(2)
                continue
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