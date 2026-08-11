"""
Приблуда на python — список акций с борда TQBR (Московская биржа).

Справочный инструмент, в рабочем цикле проекта не используется — список
отслеживаемых тикеров сейчас ведётся вручную в config.py (TRACKED_SYMBOLS).
Пригодится, если понадобится посмотреть полный список доступных акций
перед тем, как добавить новый тикер в отслеживание.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# .env лежит в корне проекта, а не в tools/ — поднимаемся на уровень выше.
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
OAUTH_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"
PLACEHOLDER = "вставь_сюда_свой_refresh_token"

# Борд TQBR = основной режим торгов акциями на Московской бирже.
BOARD = "TQBR"
EXCHANGE = "MOEX"


def make_session() -> requests.Session:
    """Создаём HTTP-сессию с автоматическими повторными попытками.

    На некоторых сетях/VPN изредка обрывается TLS-соединение
    (SSLError: UNEXPECTED_EOF_WHILE_READING) — это разовый сетевой
    сбой, а не проблема самого запроса. Вместо того чтобы падать
    с первого раза, пробуем повторить запрос ещё несколько раз
    с небольшой паузой перед тем, как сдаться.
    """
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


def get_access_token(session: requests.Session, refresh_token: str) -> str:
    response = session.post(OAUTH_URL, params={"token": refresh_token})
    response.raise_for_status()
    return response.json()["AccessToken"]


def get_tqbr_shares(session: requests.Session, access_token: str) -> list[dict]:
    """Запрашиваем у Алора все инструменты рынка FOND на Московской бирже.

    Используем /md/v2/Securities/{exchange} (а не просто /Securities):
    у этого варианта, если не указывать limit, сервер отдаёт СРАЗУ ВСЕ
    подходящие инструменты, без сортировки по объёму торгов и обрезки
    топ-N. Обычный /Securities сортирует по объёму и обрезает список,
    из-за чего в выдачу могут попасть индексы вместо акций.

    Фильтрацию до "настоящих" акций борда TQBR делаем отдельно, в
    main() — по полям board и type.
    """
    url = f"{API_URL}/md/v2/Securities/{EXCHANGE}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"market": "FOND"}
    response = session.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def main() -> None:
    if not REFRESH_TOKEN or REFRESH_TOKEN == PLACEHOLDER:
        print("Не найден настоящий ALOR_REFRESH_TOKEN.")
        print(f"Ищу файл .env здесь: {ENV_PATH}")
        if ENV_PATH.exists():
            print("Файл .env найден, но токен в нём пустой или не заменён на настоящий.")
        else:
            print("Файл .env НЕ найден по этому пути.")
            print("Проверь: файл называется ровно '.env' (без .txt в конце)")
            print("и лежит в корне проекта.")
        return

    session = make_session()

    try:
        access_token = get_access_token(session, REFRESH_TOKEN)
        print("Подключение успешно!")
        print(f"Access-токен получен, длина: {len(access_token)} символов")
    except requests.exceptions.HTTPError as e:
        print(f"Алор вернул ошибку при получении токена: {e}")
        return
    except requests.exceptions.RequestException as e:
        print(f"Не удалось связаться с Алор (после повторных попыток): {e}")
        return

    try:
        securities = get_tqbr_shares(session, access_token)

        # Настоящие акции отличаются от индексов и прочего мусора полем
        # type: "CS" — обыкновенные акции (Common Stock),
        # "PS" — привилегированные акции (Preferred Stock, например SBERP).
        # Поле board тут не помогает — у индексов (MESMTRR и т.п.) оно
        # тоже почему-то равно TQBR.
        SHARE_TYPES = {"CS", "PS"}
        shares = [
            s for s in securities
            if s.get("board") == BOARD and s.get("type") in SHARE_TYPES
        ]

        print(f"\nПолучено акций с борда {BOARD}: {len(shares)}")
        print("Первые 10 тикеров:")
        for share in shares[:10]:
            symbol = share.get("symbol")
            short_name = share.get("shortname")
            print(f"  {symbol} — {short_name}")

        found_symbols = {s.get("symbol") for s in shares}
        for check_symbol in ("SBERP", "SNGSP"):
            if check_symbol in found_symbols:
                print(f"\n{check_symbol}: найден ✔")
            else:
                raw = next((s for s in securities if s.get("symbol") == check_symbol), None)
                actual_type = raw.get("type") if raw else "тикер вообще не найден"
                print(f"\n{check_symbol}: НЕ попал в список. Его type = {actual_type!r}")
    except requests.exceptions.HTTPError as e:
        print(f"Алор вернул ошибку при получении списка акций: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Не удалось связаться с Алор (после повторных попыток): {e}")


if __name__ == "__main__":
    main()