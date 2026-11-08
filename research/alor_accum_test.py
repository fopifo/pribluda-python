"""
Тест накопителя: стрим 5 тикеров через TradeAccumulator на N секунд (дефолт 60).
Проверяет: запись в jsonl, дедуп (счётчик deduped), чтение backfill.
ДСВД сегодня до 17:00 — тестировать можно прямо сейчас.
"""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from connectors.alor.stream_client import AlorStreamClient, load_env_value
from connectors.alor.trade_accumulator import TradeAccumulator

MSK = ZoneInfo("Europe/Moscow")
TICKERS = ["SBER", "GAZP", "LKOH", "VTBR", "ROSN"]


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    refresh = load_env_value("ALOR_REFRESH_TOKEN")
    acc = TradeAccumulator()
    print(f"[accum] файл: {acc.path}")
    print(f"[accum] тикеры: {', '.join(TICKERS)}, длительность {duration}с")
    client = AlorStreamClient(refresh_token=refresh, tickers=TICKERS,
                              depth=30, on_trade=acc.on_trade)
    t = threading.Thread(target=client.run_sync, daemon=True)
    t.start()
    start = time.time()
    try:
        while time.time() - start < duration:
            time.sleep(5)
            s = acc.stats()
            el = int(time.time() - start)
            print(f"\r[accum] {el:3d}s written={s['written']:6d} "
                  f"deduped={s['deduped']:5d} tickers={s['tickers']:2d}",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    client.stop()
    time.sleep(1)
    acc.close()
    s = acc.stats()
    print(f"\n[accum] итог: written={s['written']} deduped={s['deduped']} tickers={s['tickers']}")
    bf = acc.load_backfill()
    if bf:
        f0 = datetime.fromtimestamp(bf[0]["timestamp"] / 1000, tz=MSK).strftime("%H:%M:%S")
        f1 = datetime.fromtimestamp(bf[-1]["timestamp"] / 1000, tz=MSK).strftime("%H:%M:%S")
        print(f"[backfill] сделок={len(bf)} окно={f0}-{f1} (отсортировано по timestamp)")
    else:
        print("[backfill] пусто")


if __name__ == "__main__":
    main()