"""
Приблуда на python — главное окно скринера (PySide6).
Вкладки: Роботы, Арбитраж, Графики, Фандинг.
Кнопки: Мини-окно, Настройки.
Сохранение геометрии мини-окон при закрытии.
"""
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QGridLayout, QHeaderView,
                               QHBoxLayout, QLabel, QMainWindow, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QTabWidget,
                               QPushButton)

from gui import theme
from gui.state import SharedState
from gui.mini_settings_dialog import MiniSettingsDialog
from gui.mini_window import MiniWindow

CONFIRM_REPEATS = 4
ROW_H = 20
HEADER_H = 22
CAND_H = 130
HEADERS = ["", "TICKER", "QTY", "INT", "NEXT", "MS", "LPP", "VPM", "LEN"]
# Увеличил NEXT с 70 до 90
COL_W = [34, 60, 74, 40, 90, 80, 62, 52, 36]

QSS = f"""
QWidget {{ background:{theme.BG}; color:{theme.TEXT}; font-family: '{theme.FONT_FAMILY}'; }}
QLabel {{ background:transparent; color:{theme.TEXT}; }}
QTableWidget {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; font-size:11px; gridline-color: {theme.BORDER}; }}
QHeaderView::section {{ background:{theme.PANEL}; color:{theme.MUTED}; border:none;
    border-bottom:1px solid {theme.BORDER}; padding:2px; font-weight:bold; font-size:10px; }}
QTableWidget::item {{ border:none; padding:1px; }}
QTabWidget::pane {{ border: 1px solid {theme.BORDER}; background: {theme.BG}; }}
QTabBar::tab {{ background: {theme.PANEL}; color: {theme.MUTED}; border: 1px solid {theme.BORDER}; 
                padding: 5px 15px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {theme.BG}; color: {theme.TEXT}; border-bottom: 2px solid {theme.GREEN}; }}
QPushButton {{ background: {theme.PANEL}; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; 
              padding: 2px 8px; font-size: 10px; }}
QPushButton:hover {{ background: {theme.BORDER}; }}
"""


def _is_futures(s): return "-" in s or s.endswith("F")
def _sort_key(r):
    s = r["seconds_to_next"]; return s if s is not None else float("inf")

def _metro_parts(row):
    """
    Окраска MS (jitter):
    - Зелёный: отклонение ≤ 5% от медианы
    - Жёлтый: отклонение 5-15%
    - Красный: отклонение > 15%
    """
    metro = row.get("metro")
    if not metro:
        ms = row.get("jitter_ms")
        if isinstance(ms, (int, float)):
            return [(f"{ms:.0f}", theme.TEXT)]
        return [("", theme.TEXT)]
    
    # Новые пороги: 5% и 15%
    color = {"ok": theme.GREEN, "warn": theme.YELLOW, "bad": theme.RED}
    return [(f"{ms % 1000:03d}", color[st]) for ms, st in metro]


class MainWindow(QMainWindow):
    def __init__(self, shared_state):
        super().__init__()
        self.shared_state = shared_state
        self.setWindowTitle("Приблуда на python (Qt)")
        self.resize(1400, 900)
        self._prev_keys = set()
        
        # Мини-окна (создаются по кнопке)
        self.mini_windows = {"top": None, "bottom": None}
        
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        main_lay.setContentsMargins(4, 4, 4, 4)
        main_lay.setSpacing(3)
        
        # Верхняя панель: Часы + Кнопки
        top_bar = QHBoxLayout()
        self.clock = QLabel("")
        self.clock.setFont(QFont(theme.FONT_FAMILY, 24, QFont.Bold))
        self.clock.setAlignment(Qt.AlignCenter)
        top_bar.addWidget(self.clock, 1)
        
        # Кнопка мини-окна
        self.mini_btn = QPushButton("📊 Мини-окно")
        self.mini_btn.setFixedSize(120, 25)
        self.mini_btn.clicked.connect(self._toggle_mini_windows)
        top_bar.addWidget(self.mini_btn)
        
        # Кнопка настроек
        self.settings_btn = QPushButton(" Настройки")
        self.settings_btn.setFixedSize(100, 25)
        self.settings_btn.clicked.connect(self._open_mini_settings)
        top_bar.addWidget(self.settings_btn)
        
        main_lay.addLayout(top_bar)
        
        # Вкладки
        self.tabs = QTabWidget()
        main_lay.addWidget(self.tabs, 1)
        
        # Вкладка 1: Роботы
        self.robots_tab = QWidget()
        self._init_robots_tab(self.robots_tab)
        self.tabs.addTab(self.robots_tab, "🤖 Роботы")
        
        # Вкладка 2: Арбитраж (Заглушка)
        self.arb_tab = QLabel("Раздел Арбитраж в разработке...\nЗдесь будут спреды и пары.")
        self.arb_tab.setAlignment(Qt.AlignCenter)
        self.arb_tab.setStyleSheet("font-size: 16px; color: #555;")
        self.tabs.addTab(self.arb_tab, "⚖️ Арбитраж")
        
        # Вкладка 3: Графики (Заглушка)
        self.chart_tab = QLabel("Раздел Графики в разработке...\nЗдесь будут графики тикеров.")
        self.chart_tab.setAlignment(Qt.AlignCenter)
        self.chart_tab.setStyleSheet("font-size: 16px; color: #555;")
        self.tabs.addTab(self.chart_tab, "📈 Графики")
        
        # Вкладка 4: Фандинг (Заглушка)
        self.funding_tab = QLabel("Раздел Фандинг в разработке...\nЗдесь будут ставки фандинга.")
        self.funding_tab.setAlignment(Qt.AlignCenter)
        self.funding_tab.setStyleSheet("font-size: 16px; color: #555;")
        self.tabs.addTab(self.funding_tab, "💰 Фандинг")
        
        # Таймер
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        
        self._refresh()
    
    def _toggle_mini_windows(self):
        """Открыть или закрыть мини-окна"""
        if self.mini_windows["top"] is None:
            self.mini_windows["top"] = MiniWindow(self.shared_state, row_type="top")
            self.mini_windows["top"].show()
            
            self.mini_windows["bottom"] = MiniWindow(self.shared_state, row_type="bottom")
            self.mini_windows["bottom"].show()
            
            self.mini_btn.setText("❌ Закрыть мини")
        else:
            if self.mini_windows["top"]:
                self.mini_windows["top"]._save_geometry()
                self.mini_windows["top"].close()
                self.mini_windows["top"] = None
            if self.mini_windows["bottom"]:
                self.mini_windows["bottom"]._save_geometry()
                self.mini_windows["bottom"].close()
                self.mini_windows["bottom"] = None
            
            self.mini_btn.setText("📊 Мини-окно")
    
    def _open_mini_settings(self):
        dialog = MiniSettingsDialog(self)
        dialog.exec()
    
    def _init_robots_tab(self, parent):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        
        self.t_fut_buy = self._make_table(); self.t_buy = self._make_table()
        self.t_fut_sell = self._make_table(); self.t_sell = self._make_table()
        
        lc = QVBoxLayout(); lc.setSpacing(3); lc.addWidget(self.t_fut_buy); lc.addWidget(self.t_buy)
        rc = QVBoxLayout(); rc.setSpacing(3); rc.addWidget(self.t_fut_sell); rc.addWidget(self.t_sell)
        th = QHBoxLayout(); th.setSpacing(3); th.addLayout(lc, 1); th.addLayout(rc, 1)
        lay.addLayout(th)
        
        lay.addWidget(QLabel("КАНДИДАТЫ"))
        cg = QGridLayout(); cg.setSpacing(3)
        self.t_cand_buy = self._make_table()
        self.t_cand_sell = self._make_table()
        self.t_cand_buy.setMinimumHeight(CAND_H)
        self.t_cand_buy.setMaximumHeight(CAND_H)
        self.t_cand_sell.setMinimumHeight(CAND_H)
        self.t_cand_sell.setMaximumHeight(CAND_H)
        cg.addWidget(self.t_cand_buy, 0, 0); cg.addWidget(self.t_cand_sell, 0, 1)
        lay.addLayout(cg)

    def _make_table(self):
        t = QTableWidget(0, len(HEADERS))
        t.setHorizontalHeaderLabels(HEADERS)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionMode(QTableWidget.NoSelection)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        t.setShowGrid(False)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        t.horizontalHeader().setMinimumSectionSize(30)
        for i, w in enumerate(COL_W): t.setColumnWidth(i, w)
        return t

    def _fill(self, table, rows, now_ts, batch_flash, dying_keys):
        table.setUpdatesEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            key = (row["symbol"], row["side"], row["preset"], row["start_ts"])
            base_fg = theme.MUTED if key in dying_keys else (theme.GREEN if row["side"] == "buy" else theme.RED)
            bg = None
            det = batch_flash.get(row["symbol"])
            if det is not None and now_ts - det < 6 and int((now_ts - det) * 3) % 2 == 0: bg = "#7a4a12"
            
            sec = row["seconds_to_next"]; interval = row["interval"]; lpp = row.get("price_last")
            sq = row.get("sum_qty"); st = row.get("start_ts")
            vpm = f"{sq/max((now_ts-st)/60.0,0.1):.0f}" if isinstance(sq,(int,float)) and isinstance(st,(int,float)) else ""
            
            # QTY: только min-max (два значения через дефис)
            variants = sorted(row["qty_variants"])
            if len(variants) <= 2:
                qty_str = "-".join(str(q) for q in variants)
            else:
                qty_str = f"{variants[0]}-{variants[-1]}"
            
            cells = [
                (f"{sec:.0f}s" if sec is not None else "-", theme.TEXT),
                (row["symbol"], base_fg), (qty_str, base_fg),
                (f"{interval:.0f}s" if interval is not None else "-", base_fg),
                (datetime.fromtimestamp(now_ts+sec).strftime("%H:%M") if sec is not None else "-", base_fg),
                None,
                (f"{lpp:.3f}" if isinstance(lpp,(int,float)) else "", theme.TEXT),
                (vpm, theme.TEXT), (str(row["repeats"]), base_fg)
            ]
            for c, cell in enumerate(cells):
                if c == 5:
                    parts = _metro_parts(row)
                    it = QTableWidgetItem(" ".join(p[0] for p in parts))
                    it.setForeground(QColor(parts[-1][1] if parts else theme.TEXT))
                else:
                    txt, fg = cell; it = QTableWidgetItem(txt); it.setForeground(QColor(fg))
                if bg: it.setBackground(QColor(bg))
                it.setTextAlignment(Qt.AlignCenter); table.setItem(r, c, it)
        table.setUpdatesEnabled(True)

    def _refresh(self):
        self.setUpdatesEnabled(False)
        now_ts = datetime.now().timestamp(); self.clock.setText(datetime.now().strftime("%H:%M:%S"))
        bf = self.shared_state.batch_flash or {}; rows = list(self.shared_state.rows)
        cur = {(r["symbol"], r["side"], r["preset"], r["start_ts"]) for r in rows}
        dk = self._prev_keys - cur
        
        conf = [r for r in rows if r["repeats"] >= CONFIRM_REPEATS]
        cand = [r for r in rows if r["repeats"] < CONFIRM_REPEATS]
        
        self._fill(self.t_fut_buy, sorted((r for r in conf if _is_futures(r["symbol"]) and r["side"]=="buy"), key=_sort_key), now_ts, bf, dk)
        self._fill(self.t_buy, sorted((r for r in conf if not _is_futures(r["symbol"]) and r["side"]=="buy"), key=_sort_key), now_ts, bf, dk)
        self._fill(self.t_fut_sell, sorted((r for r in conf if _is_futures(r["symbol"]) and r["side"]=="sell"), key=_sort_key), now_ts, bf, dk)
        self._fill(self.t_sell, sorted((r for r in conf if not _is_futures(r["symbol"]) and r["side"]=="sell"), key=_sort_key), now_ts, bf, dk)
        self._fill(self.t_cand_buy, sorted((r for r in cand if r["side"]=="buy"), key=_sort_key), now_ts, bf, dk)
        self._fill(self.t_cand_sell, sorted((r for r in cand if r["side"]=="sell"), key=_sort_key), now_ts, bf, dk)
        
        self._prev_keys = cur
        self.setUpdatesEnabled(True)

    def closeEvent(self, event):
        """Сохранение настроек при закрытии главного окна"""
        if self.mini_windows["top"]:
            self.mini_windows["top"]._save_geometry()
        if self.mini_windows["bottom"]:
            self.mini_windows["bottom"]._save_geometry()
        super().closeEvent(event)