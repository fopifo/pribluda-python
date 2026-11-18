"""
Приблуда на python — локальный приёмник данных перехватчиков.

POST:
  /robots            — T-Widgets  -> data/tw_robots_YYYY-MM-DD.jsonl
  /aniscan_history   — Aniscan    -> data/aniscan_robots_YYYY-MM-DD.jsonl
  /aniscan_debug     — разведка   -> data/aniscan_debug.jsonl
GET:
  /status            — счётчики за сегодня
  /bridge.html       — вкладка-мост для обхода PNA/CSP: same-origin fetch к серверу.

Формат aniscan_robots_* идентичен tools/aniscan_robot_history.py.
API Aniscan может отдавать как {page, content}, так и ГОЛЫЙ список — обрабатываем оба.
"""
import json
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")
HOST, PORT = "127.0.0.1", 8765

_aniscan_seen = {}

BRIDGE_HTML = b"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
window.addEventListener('message', function (e) {
  if (e.origin !== 'https://aniscan.ru') return;
  var d = e.data || {};
  if (!d.path || !d.obj) return;
  fetch('/' + d.path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(d.obj)
  }).then(function (r) { return r.json(); }).then(function (j) {
    try { e.source.postMessage({ __pb_ack: true, id: d.id, resp: j }, e.origin); } catch (err) {}
  }).catch(function (err) {
    try { e.source.postMessage({ __pb_ack: true, id: d.id, resp: { error: String(err) } }, e.origin); } catch (e2) {}
  });
});
</script></body></html>"""


def _rec_date(rec):
    return (rec.get("createDttm") or "")[:10]


def _extract_content(data):
    """API Aniscan: либо {page, content}, либо голый список."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        c = data.get("content")
        return c if isinstance(c, list) else []
    return []


def _aniscan_load_seen(date_str):
    if date_str in _aniscan_seen:
        return _aniscan_seen[date_str]
    seen = set()
    p = DATA_DIR / f"aniscan_robots_{date_str}.jsonl"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = r.get("id")
                if rid is not None:
                    seen.add(rid)
    _aniscan_seen[date_str] = seen
    return seen


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/bridge.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(BRIDGE_HTML)))
            self.end_headers()
            self.wfile.write(BRIDGE_HTML)
            return
        if self.path == "/status":
            today = datetime.now(MSK).date().isoformat()
            tw = DATA_DIR / f"tw_robots_{today}.jsonl"
            an = DATA_DIR / f"aniscan_robots_{today}.jsonl"
            n_tw = sum(1 for _ in open(tw, encoding="utf-8")) if tw.exists() else 0
            n_an = sum(1 for _ in open(an, encoding="utf-8")) if an.exists() else 0
            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "tw_today": n_tw, "aniscan_today": n_an}).encode("utf-8"))
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"error":"bad json"}')
            return
        if self.path == "/robots":
            self._handle_tw(payload)
        elif self.path == "/aniscan_history":
            self._handle_aniscan_history(payload)
        elif self.path == "/aniscan_debug":
            self._handle_aniscan_debug(payload)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def _handle_tw(self, payload):
        now = datetime.now(MSK)
        record = {"received_at": now.isoformat(timespec="seconds"), "payload": payload}
        out = DATA_DIR / f"tw_robots_{now.date().isoformat()}.jsonl"
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        n = len(payload.get("robots", [])) if isinstance(payload, dict) else 0
        print(f"[tw_server] +{n} robots -> {out.name}")
        self._respond({"ok": True})

    def _handle_aniscan_history(self, payload):
        now = datetime.now(MSK)
        today = now.date().isoformat()
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        window = {today, yesterday}
        content = _extract_content(payload.get("data"))
        written = 0
        dates = []
        for rec in content:
            if not isinstance(rec, dict):
                continue
            d = _rec_date(rec)
            if d:
                dates.append(d)
            if d not in window:
                continue
            rid = rec.get("id")
            if rid is None:
                continue
            seen = _aniscan_load_seen(d)
            if rid in seen:
                continue
            seen.add(rid)
            rec["_downloaded_at"] = now.isoformat()
            rec["_source_date"] = d
            out = DATA_DIR / f"aniscan_robots_{d}.jsonl"
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
        stop = bool(content) and all(d < yesterday for d in dates if d)
        if not content:
            stop = True
        if written or stop:
            print(f"[tw_server] aniscan page={payload.get('page')} written={written} stop={stop}")
        self._respond({"ok": True, "stop": stop, "written": written})

    def _handle_aniscan_debug(self, payload):
        now = datetime.now(MSK)
        record = {"received_at": now.isoformat(timespec="seconds"),
                  "url": payload.get("url"), "kind": payload.get("kind"),
                  "sample": payload.get("sample")}
        out = DATA_DIR / "aniscan_debug.jsonl"
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._respond({"ok": True})

    def _respond(self, obj):
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass


def main():
    DATA_DIR.mkdir(exist_ok=True)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"[tw_server] порт {PORT} уже занят: сервер уже запущен.")
        print("[tw_server] это окно можно закрыть.")
        input("Нажми Enter, чтобы закрыть...")
        return
    print(f"[tw_server] слушаю http://{HOST}:{PORT}  (/robots, /aniscan_history, /aniscan_debug, /bridge.html)")
    print(f"[tw_server] пишу в {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[tw_server] остановлен")
        server.server_close()


if __name__ == "__main__":
    main()