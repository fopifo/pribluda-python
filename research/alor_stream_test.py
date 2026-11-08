"""
Проверка WebSocket-ленты Алора:
  1. Получает access-токен из ALOR_REFRESH_TOKEN (refresh flow)
  2. Подписывается на N тикеров
  3. С прогресс-баром показывает принятые сделки
  4. Работает до Ctrl+C

Сегодня воскресенье — торгов нет, увидим только 200-подтверждения подписок.
В понедельник — полетят сделки.
"""
import asyncio
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from connectors.alor.stream_client import AlorStreamClient, load_env_value

MSK = timezone(timedelta(hours=3))
TICKERS_DEFAULT = ["SBER", "GAZP", "LKOH", "VTBR", "ROSN", "YDEX", "OZON", "GMKN", "PLZL", "TATN"]


class Progress:
    def __init__(self):
        self.total = 0
        self.live = 0
        self.hist = 0
        self.by_ticker = {}
        self.t_start = time.time()

    def on_trade(self, trade):
        self.total += 1
        if trade["existing"]:
            self.hist += 1
        else:
            self.live += 1
        sym = trade["symbol"]
        self.by_ticker[sym] = self.by_ticker.get(sym, 0) + 1
        self._render(trade)

    def on_status(self, msg):
        code = msg.get("httpCode", "?")
        text = msg.get("message", "")[:80]
        print(f"\r[status] httpCode={code} {text:<80}")

    def _render(self, trade):
        tag = "HIST" if trade["existing"] else "LIVE "
        elapsed = int(time.time() - self.t_start)
        rate = self.total / max(elapsed, 1)
        top = sorted(self.by_ticker.items(), key=lambda x: -x[1])[:5]
        top_str = " ".join(f"{s}:{c}" for s, c in top)
        line = (f"\r[{tag}] {trade['symbol']:6s} {trade['side']:4s} qty={trade['qty']:<5} "
                f"price={trade['price']:<8}  "
                f"| всего={self.total} live={self.live} hist={self.hist} | "
                f"{elapsed}с {rate:.1f}/с | top: {top_str}")
        print(line.ljust(140), end="", flush=True)


def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else TICKERS_DEFAULT
    try:
        refresh = load_env_value("ALOR_REFRESH_TOKEN")
    except (FileNotFoundError, KeyError) as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"[{datetime.now(MSK).strftime('%H:%M:%S')}] Тикеры ({len(tickers)}): {', '.join(tickers)}")
    print("[info] Ctrl+C — останов")
    print()

    progress = Progress()
    client = AlorStreamClient(
        refresh_token=refresh,
        tickers=tickers,
        depth=30,
        format_="Simple",
        on_trade=progress.on_trade,
        on_status=progress.on_status,
    )

    def _sigint(*_):
        print(f"\n\n[stop] {datetime.now(MSK).strftime('%H:%M:%S')} "
              f"— всего={progress.total} live={progress.live} hist={progress.hist}")
        client.stop()

    signal.signal(signal.SIGINT, _sigint)
    client.run_sync()
    print()


if __name__ == "__main__":
    main()