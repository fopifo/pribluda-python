"""
Приблуда на python — вкладка "Арбитраж".
Связки из arb_pairs.json (modules/arbitrage/pairs_config), живые цены из
data/quik_quotes.csv (lua v3). Спред и EMA-база — live_spread.
Добавление/удаление связок пишется в arb_pairs.json.
Трёхногие (symbol_c) показываются как "3 ноги: v2" и не считаются.
Архитектура: gui/tabs/arbitrage/. Не торгует, не выставляет ордера.
"""
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from connectors.quik.quotes_reader import QuotesReader
from gui import theme
from modules.arbitrage import pairs_config
from modules.arbitrage.live_spread import PairTracker, compute_spread

HEADERS = ["СВЯЗКА", "НОГА A", "ЦЕНА A", "НОГА B", "ЦЕНА B",
           "СПРЕД", "БАЗА (EMA)", "ОТКЛОН.", "СТАТУС"]

HELP_TEXT = ("СПРЕД: absolute_rub = цена B − цена A (руб); ratio_pct = (B/A − 1)·100 (%).  "
             "БАЗА: EMA спреда, память = half_life_sec.  "
             "ОТКЛОН. = СПРЕД − БАЗА.  "
             "СТАТУС «ПРОСТРЕЛ» при |отклон.| ≥ порога связки.")


def _f(v, nd=3):
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}"
    return "-"


class AddPairDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Новая арбитражная связка")
        self.existing = existing or {}
        form = QFormLayout(self)
        self.name_ed = QLineEdit()
        self.sym_a_ed = QLineEdit()
        self.sym_b_ed = QLineEdit()
        self.sym_c_ed = QLineEdit()
        self.sym_c_ed.setPlaceholderText("опционально (трёхногая, v2)")
        self.mode_cb = QComboBox()
        self.mode_cb.addItems(list(pairs_config.MODES))
        self.thr_sb = QDoubleSpinBox()
        self.thr_sb.setRange(0.001, 100000.0)
        self.thr_sb.setDecimals(3)
        self.thr_sb.setValue(0.5)
        self.hl_sb = QSpinBox()
        self.hl_sb.setRange(30, 86400)
        self.hl_sb.setValue(600)
        form.addRow("Название:", self.name_ed)
        form.addRow("Нога A:", self.sym_a_ed)
        form.addRow("Нога B:", self.sym_b_ed)
        form.addRow("Нога C:", self.sym_c_ed)
        form.addRow("Режим:", self.mode_cb)
        form.addRow("Порог:", self.thr_sb)
        form.addRow("Полураспад, сек:", self.hl_sb)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result_pair(self):
        name = self.name_ed.text().strip()
        a = self.sym_a_ed.text().strip().upper()
        b = self.sym_b_ed.text().strip().upper()
        c = self.sym_c_ed.text().strip().upper()
        if not name or not a or not b:
            return None, "Заполни название, ногу A и ногу B"
        if name in self.existing:
            return None, f"Связка '{name}' уже есть"
        pair = {
            "symbol_a": a,
            "symbol_b": b,
            "mode": self.mode_cb.currentText(),
            "threshold": float(self.thr_sb.value()),
            "half_life_sec": int(self.hl_sb.value()),
        }
        if c:
            pair["symbol_c"] = c
        return (name, pair), None


class ArbitrageTab(QWidget):
    def __init__(self, shared_state=None):
        super().__init__()
        self.reader = QuotesReader()
        self.pairs = {}
        self.trackers = {}
        self._load()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        top = QHBoxLayout()
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        top.addWidget(self.info_lbl, 1)
        add_btn = QPushButton("+ Связка")
        add_btn.clicked.connect(self._on_add)
        top.addWidget(add_btn)
        del_btn = QPushButton("− Удалить")
        del_btn.clicked.connect(self._on_delete)
        top.addWidget(del_btn)
        lay.addLayout(top)

        help_lbl = QLabel(HELP_TEXT)
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(f"color: {theme.MUTED}; font-size: 9px; background: transparent;")
        lay.addWidget(help_lbl)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        lay.addWidget(self.table, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()

    def _load(self):
        self.pairs = pairs_config.load_pairs()
        for name, pair in self.pairs.items():
            if name not in self.trackers:
                self.trackers[name] = PairTracker(pair.get("half_life_sec", 600))

    def _on_add(self):
        dlg = AddPairDialog(self, existing=self.pairs)
        if dlg.exec() != QDialog.Accepted:
            return
        (name, pair), err = dlg.result_pair()
        if err:
            self.info_lbl.setText(f"⚠ {err}")
            return
        self.pairs[name] = pair
        self._save()

    def _on_delete(self):
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            return
        names = list(self.pairs.keys())
        for r in reversed(rows):
            if r < len(names):
                n = names[r]
                self.pairs.pop(n, None)
                self.trackers.pop(n, None)
        self._save()

    def _save(self):
        try:
            pairs_config.save_pairs(self.pairs)
            self.info_lbl.setText("Сохранено в arb_pairs.json")
        except Exception as e:
            self.info_lbl.setText(f"⚠ Ошибка записи: {type(e).__name__}: {e}")
        self._load()
        self._refresh()

    def _refresh(self):
        try:
            quotes = self.reader.read()
            now = time.time()
            self.table.setRowCount(len(self.pairs))
            for r, (name, pair) in enumerate(self.pairs.items()):
                a = pair.get("symbol_a")
                b = pair.get("symbol_b")
                leg_c = pair.get("symbol_c")
                qa = quotes.get(a, {}).get("last")
                qb = quotes.get(b, {}).get("last")
                spread = compute_spread(pair.get("mode"), qa, qb)
                tr = self.trackers.get(name)
                if spread is not None and tr is not None:
                    tr.update(spread, now)
                dev = tr.deviation() if tr else None
                thr = pair.get("threshold", 0.5)
                if leg_c:
                    status, fg = "3 ноги: v2", theme.MUTED
                elif spread is None:
                    status, fg = "нет данных", theme.MUTED
                elif dev is not None and abs(dev) >= thr:
                    status, fg = "ПРОСТРЕЛ", theme.RED
                else:
                    status, fg = "норма", theme.GREEN
                vals = [name, a, _f(qa), b, _f(qb),
                        _f(spread), _f(tr.ema if tr else None),
                        _f(dev), status]
                for cidx, v in enumerate(vals):
                    it = QTableWidgetItem(v)
                    it.setForeground(QColor(fg if cidx == 8 else theme.TEXT))
                    self.table.setItem(r, cidx, it)
        except Exception as e:
            self.info_lbl.setText(f"⚠ {type(e).__name__}: {e}")