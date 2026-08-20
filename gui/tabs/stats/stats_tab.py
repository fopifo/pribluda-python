"""
Приблуда на python — вкладка "Статистика" (Н-005).
Читает data/robots_history.jsonl (пишет детектор, Н-010) и агрегирует:
по тикеру — сколько раз, сторона, средний интервал, самый частый
день недели и час включения ("когда готовят робота").
Архитектура: gui/tabs/stats/.
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from gui import theme

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_FILE = BASE_DIR / "data" / "robots_history.jsonl"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _load_history():
    rows = []
    if not HISTORY_FILE.exists():
        return rows
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


class StatsTab(QWidget):
    def __init__(self, shared_state=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        top = QHBoxLayout()
        title = QLabel("📊 СТАТИСТИКА РОБОТОВ (когда включают)")
        title.setStyleSheet(f"color: {theme.TEXT}; font-weight: bold; background: transparent;")
        top.addWidget(title)
        self.search_ed = QLineEdit()
        self.search_ed.setPlaceholderText("Фильтр по тикеру...")
        self.search_ed.setFixedWidth(140)
        self.search_ed.textChanged.connect(self._refresh)
        top.addWidget(self.search_ed)
        reload_btn = QPushButton("⟳ Обновить")
        reload_btn.clicked.connect(self._refresh)
        top.addWidget(reload_btn)
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        top.addWidget(self.info_lbl, 1)
        lay.addLayout(top)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ТИКЕР", "СТОРОНА", "РАЗ", "СР.ИНТ", "ДЕНЬ(чаще)", "ЧАС(чаще)", "ПЕРВЫЙ", "ПОСЛЕДНИЙ"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        lay.addWidget(self.table, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(10000)
        self._refresh()

    def _refresh(self):
        rows = _load_history()
        flt = self.search_ed.text().strip().upper()

        agg = defaultdict(lambda: {
            "count": 0, "ints": [], "wd": defaultdict(int), "hr": defaultdict(int),
            "first": None, "last": None,
        })
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if not sym:
                continue
            if flt and flt not in sym:
                continue
            side = r.get("side", "?")
            key = (sym, side)
            a = agg[key]
            a["count"] += 1
            iv = r.get("interval_avg")
            if isinstance(iv, (int, float)):
                a["ints"].append(iv)
            ts = r.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    a["wd"][dt.weekday()] += 1
                    a["hr"][dt.hour] += 1
                    if a["first"] is None or dt < a["first"]:
                        a["first"] = dt
                    if a["last"] is None or dt > a["last"]:
                        a["last"] = dt
                except ValueError:
                    pass

        items = sorted(agg.items(), key=lambda kv: (-kv[1]["count"], kv[0][0]))
        self.table.setRowCount(len(items))
        for r, ((sym, side), a) in enumerate(items):
            avg_iv = f"{sum(a['ints'])/len(a['ints']):.0f}s" if a["ints"] else "-"
            wd = WEEKDAYS[max(a["wd"], key=a["wd"].get)] if a["wd"] else "-"
            hr = f"{max(a['hr'], key=a['hr'].get):02d}:00" if a["hr"] else "-"
            first = a["first"].strftime("%d.%m %H:%M") if a["first"] else "-"
            last = a["last"].strftime("%d.%m %H:%M") if a["last"] else "-"
            side_color = theme.GREEN if side == "buy" else theme.RED
            vals = [sym, side, str(a["count"]), avg_iv, wd, hr, first, last]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setForeground(QColor(side_color))
                else:
                    it.setForeground(QColor(theme.TEXT))
                self.table.setItem(r, c, it)

        self.info_lbl.setText(f"записей: {len(rows)}")