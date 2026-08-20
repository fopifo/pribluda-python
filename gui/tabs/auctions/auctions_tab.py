"""
Приблуда на python — вкладка "Аукционы" (v4).
- Расписание сессий MOEX с обратным отсчётом;
- группы тикеров (эшелоны) — вертикальные колонки;
- статус "АУКЦИОН": из Quik (OPENPERIOD) ИЛИ по расписанию, если флаг
  не пришёл (v4: аукцион закрытия тоже виден);
- "прочие события" = тикеры ВНЕ твоих групп, по которым прямо сейчас
  аукцион или приостановка торговли (отключается галочкой);
- настройки тикеров ("в игре", мьют, группы) — "⚙ Настройки";
  ошибки настроек теперь видны в шапке вкладки (v4).
Архитектура: gui/tabs/auctions/.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from connectors.quik.quotes_reader import QuotesReader
from core import auction_settings as cfg_mod
from gui import theme
from gui.tabs.auctions.auction_manager import AuctionManagerDialog

MSK = ZoneInfo("Europe/Moscow")

SCHEDULE = [
    (6 * 60 + 50, 7 * 60, "Аукцион открытия (утренняя)"),
    (7 * 60, 10 * 60, "Утренняя сессия"),
    (10 * 60, 18 * 60 + 40, "Основная сессия"),
    (18 * 60 + 40, 18 * 60 + 50, "Аукцион закрытия"),
]

COL_HEADERS = ["ТИКЕР", "ПОСЛЕД.", "ОБЪЁМ", "СТАТУС"]
COL_W = [62, 62, 62, 108]


def _mm(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _fmt_dur(sec):
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _f(v):
    return f"{v:g}" if isinstance(v, (int, float)) else "-"


def _i(v):
    return f"{int(v)}" if isinstance(v, (int, float)) else "-"


class AuctionsTab(QWidget):
    def __init__(self, shared_state=None):
        super().__init__()
        self.reader = QuotesReader()
        self.cfg = cfg_mod.load_auction_settings()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        top = QHBoxLayout()
        self.phase_lbl = QLabel("")
        self.phase_lbl.setStyleSheet(
            f"color: {theme.YELLOW}; font-size: 12px; font-weight: bold; "
            f"background: transparent;")
        top.addWidget(self.phase_lbl, 1)
        self.show_muted_cb = QCheckBox("🔇 замьюченные")
        self.show_muted_cb.setToolTip("Показывать замьюченные тикеры серым")
        self.show_muted_cb.setChecked(bool(self.cfg.get("show_muted")))
        self.show_muted_cb.toggled.connect(self._on_flag_toggled)
        top.addWidget(self.show_muted_cb)
        self.show_ung_cb = QCheckBox("прочие события")
        self.show_ung_cb.setToolTip(
            "Прочие события = тикеры ВНЕ твоих групп, по которым прямо "
            "сейчас аукцион или приостановка торговли")
        self.show_ung_cb.setChecked(bool(self.cfg.get("show_ungrouped")))
        self.show_ung_cb.toggled.connect(self._on_flag_toggled)
        top.addWidget(self.show_ung_cb)
        set_btn = QPushButton("⚙ Настройки")
        set_btn.clicked.connect(self._open_settings)
        top.addWidget(set_btn)
        lay.addLayout(top)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        lay.addWidget(self.count_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.cols_layout = QHBoxLayout(self.scroll_content)
        self.cols_layout.setContentsMargins(0, 0, 0, 0)
        self.cols_layout.setSpacing(4)
        self.scroll.setWidget(self.scroll_content)
        lay.addWidget(self.scroll, 1)

        self._rebuild_columns()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()

    # --- структура -----------------------------------------------------
    def _make_col_table(self):
        t = QTableWidget(0, len(COL_HEADERS))
        t.setHorizontalHeaderLabels(COL_HEADERS)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionMode(QTableWidget.NoSelection)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        for i, w in enumerate(COL_W):
            t.setColumnWidth(i, w)
        return t

    def _rebuild_columns(self):
        while self.cols_layout.count():
            item = self.cols_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.columns = []  # (head_lbl, tickers, table)
        for gi, g in enumerate(self.cfg["groups"]):
            color = cfg_mod.group_color(gi)
            col_w = QWidget()
            vl = QVBoxLayout(col_w)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(0)
            head = QLabel("")
            head.setStyleSheet(f"color: {color}; font-weight: bold; "
                               f"font-size: 11px; background: transparent;")
            vl.addWidget(head)
            table = self._make_col_table()
            tickers = [t for t in g["tickers"]
                       if self.show_muted_cb.isChecked()
                       or t not in self.cfg["muted"]]
            table.setRowCount(len(tickers))
            vl.addWidget(table, 1)
            self.cols_layout.addWidget(col_w, 1)
            self.columns.append((head, tickers, table))

        ev_w = QWidget()
        ev_l = QVBoxLayout(ev_w)
        ev_l.setContentsMargins(0, 0, 0, 0)
        ev_l.setSpacing(0)
        self.events_head = QLabel("")
        self.events_head.setStyleSheet(
            f"color: {theme.MUTED}; font-weight: bold; font-size: 11px; "
            f"background: transparent;")
        ev_l.addWidget(self.events_head)
        self.events_table = self._make_col_table()
        ev_l.addWidget(self.events_table, 1)
        if self.cfg.get("show_ungrouped", True):
            self.cols_layout.addWidget(ev_w, 1)

    def _on_flag_toggled(self, _=None):
        self.cfg["show_muted"] = self.show_muted_cb.isChecked()
        self.cfg["show_ungrouped"] = self.show_ung_cb.isChecked()
        try:
            cfg_mod.save_auction_settings(self.cfg)
        except Exception:
            pass
        self._rebuild_columns()
        self._refresh()

    def _open_settings(self):
        try:
            dlg = AuctionManagerDialog(self)
            dlg.exec()
            self.cfg = cfg_mod.load_auction_settings()
            self.show_muted_cb.blockSignals(True)
            self.show_ung_cb.blockSignals(True)
            self.show_muted_cb.setChecked(bool(self.cfg.get("show_muted")))
            self.show_ung_cb.setChecked(bool(self.cfg.get("show_ungrouped")))
            self.show_muted_cb.blockSignals(False)
            self.show_ung_cb.blockSignals(False)
            self._rebuild_columns()
            self._refresh()
            self.phase_lbl.setStyleSheet(
                f"color: {theme.YELLOW}; font-size: 12px; font-weight: bold; "
                f"background: transparent;")
        except Exception as e:
            self.phase_lbl.setText(f"⚠ Ошибка настроек: {type(e).__name__}: {e}")
            self.phase_lbl.setStyleSheet(
                "color: #ff4444; font-size: 12px; font-weight: bold; "
                "background: transparent;")

    # --- обновление ----------------------------------------------------
    def _status_of(self, q, phase_name):
        if q:
            op = q.get("openperiod")
            if op is not None and op == 1:
                return "АУКЦИОН", theme.YELLOW
            st = q.get("tradingstatus")
            if st is not None and st != 1:
                return "приостановлена", theme.RED
        # v4: если идёт аукцион по расписанию, а флаг OPENPERIOD не пришёл
        if phase_name and "аукцион" in phase_name.lower():
            return "АУКЦИОН (распис.)", theme.YELLOW
        if not q:
            return "—", theme.MUTED
        return "—", theme.MUTED

    def _fill_row(self, table, r, t, q, muted, phase_name):
        status, fg = self._status_of(q, phase_name)
        if muted:
            status, fg = "🔇 " + status, theme.MUTED
        vals = [t, _f(q.get("last") if q else None),
                _i(q.get("voltoday") if q else None), status]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setForeground(QColor(fg))
            table.setItem(r, c, it)

    def _refresh(self):
        try:
            now = datetime.now(MSK)
            mins = now.hour * 60 + now.minute
            cur_sec = mins * 60 + now.second
            phase_txt = "торги закрыты"
            for start, end, name in SCHEDULE:
                if start <= mins < end:
                    phase_txt = f"{name} ({_mm(start)}–{_mm(end)})"
                    break
            next_txt = "Все сессии завершены, ждём следующий день"
            for start, end, name in SCHEDULE:
                if cur_sec < start * 60:
                    next_txt = f"До '{name}': {_fmt_dur(start * 60 - cur_sec)}"
                    break
            if not self.phase_lbl.text().startswith("⚠"):
                self.phase_lbl.setText(f"Сейчас: {phase_txt}   |   {next_txt}")

            quotes = self.reader.read()
            grouped = set()
            for g in self.cfg["groups"]:
                grouped.update(g["tickers"])

            for head, tickers, table in self.columns:
                act = 0
                for r, t in enumerate(tickers):
                    q = quotes.get(t)
                    muted = t in self.cfg["muted"]
                    st, _ = self._status_of(q, phase_txt)
                    if st.startswith("АУКЦИОН") and not muted:
                        act += 1
                    self._fill_row(table, r, t, q, muted, phase_txt)
                head.setText(f"● {act} из {len(tickers)} в аукционе")

            rows = []
            for t in sorted(quotes):
                if t in grouped:
                    continue
                if t in self.cfg["muted"] and not self.show_muted_cb.isChecked():
                    continue
                q = quotes[t]
                op = q.get("openperiod")
                st = q.get("tradingstatus")
                if (op is not None and op == 1) or (st is not None and st != 1):
                    rows.append((t, q, t in self.cfg["muted"]))
            self.events_head.setText(f"Прочие события: {len(rows)}")
            self.events_table.setRowCount(len(rows))
            for r, (t, q, muted) in enumerate(rows):
                self._fill_row(self.events_table, r, t, q, muted, phase_txt)
        except Exception as e:
            self.phase_lbl.setText(f"Ошибка обновления: {type(e).__name__}: {e}")