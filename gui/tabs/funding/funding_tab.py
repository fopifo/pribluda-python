"""
Приблуда на python — вкладка "Фандинг".
Ставки свопа фьючерсов через MOEX ISS (integrations/funding_iss).
Обновление раз в 60 c в фоновом потоке; GUI только читает кэш.
Архитектура: gui/tabs/funding/.
"""
import threading
import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from gui import theme
from integrations import funding_iss


class FundingTab(QWidget):
    def __init__(self, shared_state=None):
        super().__init__()
        self.rows = []
        self.error = None
        self.updated_at = None
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.info_lbl = QLabel("Фандинг: загрузка из MOEX ISS...")
        self.info_lbl.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        lay.addWidget(self.info_lbl)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["SECID", "SWAPRATE", "SYSTIME"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        lay.addWidget(self.table, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_gui)
        self.timer.start(5000)
        self._refresh_gui()

    def _loop(self):
        while not self._stop.is_set():
            rows, err = funding_iss.fetch_funding()
            self.rows, self.error = rows, err
            self.updated_at = time.time()
            self._stop.wait(60)

    def _refresh_gui(self):
        if self.error and not self.rows:
            self.info_lbl.setText(f"Фандинг: MOEX ISS недоступен ({self.error})")
            self.table.setRowCount(0)
            return
        if self.updated_at:
            ts = datetime.fromtimestamp(self.updated_at).strftime("%H:%M:%S")
            self.info_lbl.setText(f"Фандинг (ставки свопа фьючерсов), обновлено {ts}")
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            swap = row.get("swaprate")
            vals = [str(row.get("secid") or ""),
                    f"{swap:.2f}" if isinstance(swap, (int, float)) else "-",
                    str(row.get("systime") or "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1 and isinstance(swap, (int, float)):
                    it.setForeground(QColor(theme.GREEN if swap >= 0 else theme.RED))
                self.table.setItem(r, c, it)