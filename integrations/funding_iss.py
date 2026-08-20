"""
Приблуда на python — ставки свопа (фандинг) фьючерсов через MOEX ISS
(публично, без токена). Колонки ищем ПО ИМЕНАМ в ответе, а не по позициям.
СПИСОК ИНСТРУМЕНТОВ расширен по пробному запросу 2026-08-20 (iss.moex.com):
все фьючерсы, у которых ISS отдаёт SWAPRATE.
Архитектура: integrations/. Только чтение.
"""
import requests

ISS_URL = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
DEFAULT_SECIDS = ("CNYRUBF,EURRUBF,GAZPF,GLDRUBF,IMOEXF,RGBIF,"
                  "SBERF,SLVRUBF,USDRUBF")


def fetch_funding(secids=DEFAULT_SECIDS, timeout=10):
    """Возвращает (rows, error). rows: список {secid, swaprate, systime}."""
    try:
        resp = requests.get(
            ISS_URL,
            params={"securities": secids, "iss.only": "marketdata"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        md = (data or {}).get("marketdata") or {}
        cols = md.get("columns") or []
        rows_raw = md.get("data") or []
        idx = {name: i for i, name in enumerate(cols)}
        rows = []
        for r in rows_raw:
            def get(name):
                i = idx.get(name)
                if i is None or i >= len(r):
                    return None
                return r[i]
            secid = get("SECID")
            if not secid:
                continue
            swap = get("SWAPRATE")
            try:
                swap = float(swap) if swap is not None else None
            except (TypeError, ValueError):
                swap = None
            rows.append({"secid": secid, "swaprate": swap, "systime": get("SYSTIME")})
        return rows, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"