"""
Приблуда на python — локальный приёмник данных перехватчика T-Widgets.
Принимает POST от research/t-widgets/tw_interceptor.user.js и пишет
сырые пакеты роботов в data/tw_robots_YYYY-MM-DD.jsonl.

v2 (2026-09-03): если порт 8765 занят (сервер уже запущен вручную) —
не падаем с traceback, а сообщаем и ждём Enter; окно можно закрыть.

Использование (из корня проекта):
    python tools/tw_server.py
Слушает http://127.0.0.1:8765
"""
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")
HOST, PORT = "127.0.0.1", 8765


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            today = DATA_DIR / f"tw_robots_{datetime.now(MSK).date().isoformat()}.jsonl"
            n = 0
            if today.exists():
                with open(today, "r", encoding="utf-8") as f:
                    n = sum(1 for _ in f)
            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "records_today": n}).encode("utf-8"))
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def do_POST(self):
        if self.path != "/robots":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"error":"bad json"}')
            print(f"[tw_server] bad json: {e}")
            return

        now = datetime.now(MSK)
        record = {"received_at": now.isoformat(timespec="seconds"), "payload": payload}
        out = DATA_DIR / f"tw_robots_{now.date().isoformat()}.jsonl"
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        n_robots = len(payload.get("robots", [])) if isinstance(payload, dict) else 0
        print(f"[tw_server] +{n_robots} robots -> {out.name}")
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt, *args):
        pass  # не шумим на служебные запросы


def main():
    DATA_DIR.mkdir(exist_ok=True)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"[tw_server] порт {PORT} уже занят: сервер уже запущен.")
        print("[tw_server] это окно можно закрыть.")
        print(f"[tw_server] ({e})")
        input("Нажми Enter, чтобы закрыть...")
        return
    print(f"[tw_server] слушаю http://{HOST}:{PORT}/robots")
    print(f"[tw_server] пишу в {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[tw_server] остановлен")
        server.server_close()


if __name__ == "__main__":
    main()