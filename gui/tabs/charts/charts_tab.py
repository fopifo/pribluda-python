"""
Приблуда на python — вкладка "Графики" (v2.1).
Живой график по data/quik_quotes.csv (lua v3):
  * цена тикера — тикер вводится с клавиатуры (или быстрый выбор);
  * спред двух любых тикеров: "T1 − T2" (рубли) или "T1 / T2".
Ось цен — СПРАВА. История копится в памяти по каждому ключу.
v2.1: скрытая вкладка не перерисовывает matplotlib (производительность).
v3 (позже): свечи из Quik через CreateDataSource.
Архитектура: gui/tabs/charts/.
"""
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from connectors.quik.quotes_reader import QuotesReader
from gui import theme
from modules.arbitrage import pairs_config
from modules.arbitrage.live_spread import compute_spread


class ChartsTab(QWidget):
    def __init__(self, shared_state=None):
        super().__init__()
        self.reader = QuotesReader()
        self.hist = {}  # ключ -> deque[(ts, value)]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel("Режим:"))
        self.mode_cb = QComboBox()
        self.mode_cb.addItems(["Цена тикера", "Спред: T1 − T2 (руб)", "Отношение: T1 / T2"])
        self.mode_cb.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self.mode_cb)

        top.addWidget(QLabel("T1:"))
        self.t1_ed = QLineEdit("SBER")
        self.t1_ed.setFixedWidth(70)
        top.addWidget(self.t1_ed)

        self.t2_lbl = QLabel("T2:")
        top.addWidget(self.t2_lbl)
        self.t2_ed = QLineEdit("GAZP")
        self.t2_ed.setFixedWidth(70)
        top.addWidget(self.t2_ed)

        top.addWidget(QLabel("Быстрый выбор:"))
        self.quick_cb = QComboBox()
        self.quick_cb.currentTextChanged.connect(self._on_quick_pick)
        top.addWidget(self.quick_cb, 1)
        lay.addLayout(top)

        self.fig = Figure(figsize=(8, 4))
        self.fig.patch.set_facecolor(theme.BG)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay.addWidget(self.canvas, 1)

        self._on_mode_changed()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2000)
        self._refresh()

    def _on_mode_changed(self):
        spread_mode = self.mode_cb.currentIndex() != 0
        self.t2_lbl.setVisible(spread_mode)
        self.t2_ed.setVisible(spread_mode)

    def _on_quick_pick(self, text):
        if text:
            self.t1_ed.setText(text.strip().upper())

    def _refresh_quick_list(self):
        cur = self.quick_cb.currentText()
        items = sorted({t for t, q in self.reader.read().items()
                        if q.get("class") == "TQBR"})
        if not items:
            return
        self.quick_cb.blockSignals(True)
        self.quick_cb.clear()
        self.quick_cb.addItems(items)
        if cur in items:
            self.quick_cb.setCurrentText(cur)
        self.quick_cb.blockSignals(False)

    def _key_and_value(self, quotes):
        """Возвращает (заголовок, значение, ключ_истории)."""
        mode = self.mode_cb.currentIndex()
        t1 = self.t1_ed.text().strip().upper()
        t2 = self.t2_ed.text().strip().upper()
        if not t1:
            return None, None, None
        if mode == 0:
            q = quotes.get(t1)
            return t1, (q.get("last") if q else None), t1
        if not t2:
            return f"{t1} / {t2}", None, None
        p1 = quotes.get(t1, {}).get("last")
        p2 = quotes.get(t2, {}).get("last")
        if p1 is None or p2 is None:
            return f"{t1} vs {t2}", None, None
        if mode == 1:
            return f"{t1} − {t2}", p1 - p2, f"{t1}-{t2}"
        if p1 <= 0:
            return f"{t1} / {t2}", None, None
        return f"{t1} / {t2}", p1 / p2, f"{t1}/{t2}"

    def _refresh(self):
        # v2.1: скрытая вкладка спит — matplotlib не молотит впустую
        if not self.isVisible():
            return
        try:
            if self.quick_cb.count() == 0:
                self._refresh_quick_list()
            quotes = self.reader.read()
            title, value, hkey = self._key_and_value(quotes)
            now = time.time()
            if hkey and value is not None:
                d = self.hist.setdefault(hkey, deque(maxlen=3600))
                if not d or d[-1][1] != value or now - d[-1][0] >= 2:
                    d.append((now, value))

            self.ax.clear()
            self.ax.set_facecolor(theme.BG)
            self.ax.yaxis.tick_right()                 # ось цен СПРАВА
            self.ax.yaxis.set_label_position("right")

            d = self.hist.get(hkey) if hkey else None
            if d and len(d) >= 2:
                xs = [p[0] for p in d]
                ys = [p[1] for p in d]
                color = theme.GREEN if self.mode_cb.currentIndex() == 0 else theme.YELLOW
                self.ax.plot(xs, ys, color=color, linewidth=1.0)
                self.ax.set_xticks([xs[0], xs[-1]])
                self.ax.set_xticklabels([
                    datetime.fromtimestamp(xs[0]).strftime("%H:%M:%S"),
                    datetime.fromtimestamp(xs[-1]).strftime("%H:%M:%S")])
                last = ys[-1]
                self.ax.axhline(last, color=color, linewidth=0.5, alpha=0.4)
                self.ax.set_title(f"{title}   посл: {last:.4g}",
                                  color=theme.TEXT, fontsize=10)
            else:
                self.ax.set_title(f"{title or 'введите тикер'} — накопление данных...",
                                  color=theme.MUTED, fontsize=10)
            self.ax.tick_params(colors=theme.MUTED, labelsize=8)
            for s in self.ax.spines.values():
                s.set_color(theme.BORDER)
            self.ax.grid(True, alpha=0.15)
            self.canvas.draw()
        except Exception as e:
            try:
                self.ax.set_title(f"Ошибка: {type(e).__name__}: {e}",
                                  color=theme.RED, fontsize=9)
                self.canvas.draw()
            except Exception:
                pass