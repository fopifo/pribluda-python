"""
Приблуда на python — главное окно скринера (PySide6).
Минималистичный дизайн, 4 блока в ряд, группировка по тикерам.
Системный трей, копирование тикера по двойному клику, поиск.
ДОБАВЛЕНО: колонка CD (обратный отсчёт), NEXT = время удара МИН:СЕК,
просроченные роботы показываются красным.
Вкладки: Роботы, Планки, Аукционы, Арбитраж, Фандинг.
Звуковые уведомления с возможностью отключения.
"""
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QGridLayout, QHeaderView,
                               QHBoxLayout, QLabel, QMainWindow, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QTabWidget,
                               QPushButton, QScrollArea, QSystemTrayIcon, QMenu,
                               QStyle, QFrame, QLineEdit, QMessageBox)

from gui import theme
from gui.state import SharedState
from gui.mini_settings_dialog import MiniSettingsDialog
from gui.ticker_manager import TickerManagerDialog
from gui.mini_window import MiniWindow
from gui.tabs.limits.limits_tab import LimitsTab
from core.sound_manager import SoundManager

CONFIRM_REPEATS = 4
ROW_H = 16
HEADERS = ["CD", "TICKER", "QTY", "INT", "NEXT", "MS", "LPP", "VPM", "LEN"]
COL_W = [34, 55, 55, 38, 55, 65, 55, 45, 32]

QSS = f"""
QWidget {{ background:{theme.BG}; color:{theme.TEXT}; font-family: '{theme.FONT_FAMILY}'; }}
QLabel {{ background:transparent; color:{theme.TEXT}; }}
QTableWidget {{ background:{theme.BG}; border:none; font-size:10px; outline:none; }}
QHeaderView::section {{ background:{theme.BG}; color:{theme.MUTED}; border:none;
    border-bottom:1px solid {theme.BORDER}; padding:2px 4px; font-weight:bold; font-size:9px; }}
QTableWidget::item {{ border:none; padding:1px 2px; }}
QTableWidget::item:selected {{ background:{theme.BORDER}; }}
QTabWidget::pane {{ border: none; background: {theme.BG}; }}
QTabBar::tab {{ background: {theme.PANEL}; color: {theme.MUTED}; border: none; 
                padding: 4px 12px; margin-right: 2px; font-size: 11px; }}
QTabBar::tab:selected {{ background: {theme.BG}; color: {theme.TEXT}; border-bottom: 2px solid {theme.GREEN}; }}
QPushButton {{ background: {theme.PANEL}; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; 
              padding: 2px 8px; font-size: 10px; border-radius: 2px; }}
QPushButton:hover {{ background: {theme.BORDER}; }}
QScrollBar:vertical {{ border: none; background: {theme.BG}; width: 4px; }}
QScrollBar::handle:vertical {{ background: {theme.BORDER}; border-radius: 2px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ border: none; background: {theme.BG}; height: 4px; }}
QScrollBar::handle:horizontal {{ background: {theme.BORDER}; border-radius: 2px; min-width: 20px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QLineEdit {{ background: {theme.PANEL}; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; 
            padding: 2px 6px; font-size: 10px; border-radius: 2px; }}
QFrame {{ border: none; }}
"""

def _is_futures(s): return "-" in s or s.endswith("F")
def _sort_key(r):
    s = r["seconds_to_next"]; return s if s is not None else float("inf")

def _metro_parts(row):
    metro = row.get("metro")
    if not metro:
        ms = row.get("jitter_ms")
        if isinstance(ms, (int, float)): return [(f"{ms:.0f}", theme.TEXT)]
        return [("", theme.TEXT)]
    color = {"ok": theme.GREEN, "warn": theme.YELLOW, "bad": theme.RED}
    return [(f"{ms % 1000:03d}", color[st]) for ms, st in metro]


class MainWindow(QMainWindow):
    def __init__(self, shared_state):
        super().__init__()
        self.shared_state = shared_state
        self.setWindowTitle("Приблуда")
        self.resize(1600, 900)
        self._prev_keys = set()
        self.mini_windows = {"top": None, "bottom": None}
        self.search_text = ""
        
        # Звуковой менеджер
        self.sound_manager = SoundManager()
        
        self._setup_tray()
        
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)
        
        # Верхняя панель: часы по центру, кнопки справа
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(4, 2, 4, 2)
        
        top_bar.addStretch(1)
        
        self.clock = QLabel("")
        self.clock.setFont(QFont(theme.FONT_FAMILY, 14, QFont.Bold))
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setStyleSheet("padding: 0 10px;")
        top_bar.addWidget(self.clock)
        
        top_bar.addStretch(1)
        
        # Поиск
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(" Поиск тикера...")
        self.search_input.setFixedWidth(130)
        self.search_input.textChanged.connect(self._on_search_changed)
        top_bar.addWidget(self.search_input)
        
        # Кнопка звука
        self.sound_btn = QPushButton("🔊")
        self.sound_btn.setFixedSize(35, 24)
        self.sound_btn.setToolTip("Включить/выключить звуки")
        self.sound_btn.clicked.connect(self._toggle_sound)
        self._update_sound_button()
        top_bar.addWidget(self.sound_btn)
        
        self.mini_btn = QPushButton("📊 Мини")
        self.mini_btn.setFixedSize(70, 24)
        self.mini_btn.clicked.connect(self._toggle_mini_windows)
        top_bar.addWidget(self.mini_btn)
        
        self.settings_btn = QPushButton("⚙ Роботы")
        self.settings_btn.setFixedSize(75, 24)
        self.settings_btn.clicked.connect(self._open_ticker_settings)
        top_bar.addWidget(self.settings_btn)
        
        self.mini_settings_btn = QPushButton(" Мини")
        self.mini_settings_btn.setFixedSize(70, 24)
        self.mini_settings_btn.clicked.connect(self._open_mini_settings)
        top_bar.addWidget(self.mini_settings_btn)
        
        main_lay.addLayout(top_bar)
        
        # Вкладки
        self.tabs = QTabWidget()
        main_lay.addWidget(self.tabs, 1)
        
        # Вкладка Роботы
        self.robots_tab = QWidget()
        self._init_robots_tab(self.robots_tab)
        self.tabs.addTab(self.robots_tab, "🤖 Роботы")
        
        # Вкладка Планки
        self.limits_tab_widget = QWidget()
        self._init_limits_tab(self.limits_tab_widget)
        self.tabs.addTab(self.limits_tab_widget, "📏 Планки")
        
        # Заглушки
        self.tabs.addTab(self._placeholder("Аукционы: в разработке"), "🔔 Аукционы")
        self.tabs.addTab(self._placeholder("Арбитраж: в разработке"), "⚖️ Арбитраж")
        self.tabs.addTab(self._placeholder("Фандинг: в разработке"), " Фандинг")
        self.tabs.addTab(self._placeholder("Графики: в разработке"), " Графики")
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()
    
    def _on_search_changed(self, text):
        self.search_text = text.strip().upper()
        self._refresh()
    
    def _toggle_sound(self):
        """Переключение звуков"""
        self.sound_manager.toggle()
        self._update_sound_button()
        
        status = "включены" if self.sound_manager.enabled else "выключены"
        self.statusBar().showMessage(f"🔊 Звуки {status}", 2000)
    
    def _update_sound_button(self):
        """Обновление иконки кнопки звука"""
        if self.sound_manager.enabled:
            self.sound_btn.setText("🔊")
            self.sound_btn.setStyleSheet("""
                QPushButton { background: #1a3a1a; color: #00ff00; border: 1px solid #2a5a2a; }
                QPushButton:hover { background: #2a5a2a; }
            """)
        else:
            self.sound_btn.setText("🔇")
            self.sound_btn.setStyleSheet("""
                QPushButton { background: #3a1a1a; color: #ff4444; border: 1px solid #5a2a2a; }
                QPushButton:hover { background: #5a2a2a; }
            """)
    
    def _copy_ticker(self, item):
        ticker = item.text()
        if not ticker or ticker in HEADERS:
            return
        QApplication.clipboard().setText(ticker)
        self.statusBar().showMessage(f"📋 Тикер '{ticker}' скопирован", 1500)
    
    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxInformation))
        
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Показать окно")
        show_action.triggered.connect(self.showNormal)
        
        quit_action = tray_menu.addAction("Выход")
        quit_action.triggered.connect(self._quit_app)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()
    
    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
    
    def _quit_app(self):
        if self.mini_windows["top"]:
            self.mini_windows["top"].close()
        if self.mini_windows["bottom"]:
            self.mini_windows["bottom"].close()
        QApplication.quit()
    
    def _placeholder(self, text):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 14px; color: #555;")
        return lbl

    def _toggle_mini_windows(self):
        if self.mini_windows["top"] is None:
            self.mini_windows["top"] = MiniWindow(self.shared_state, row_type="top")
            self.mini_windows["top"].show()
            self.mini_windows["bottom"] = MiniWindow(self.shared_state, row_type="bottom")
            self.mini_windows["bottom"].show()
            self.mini_btn.setText("▌ Мини")
        else:
            for key in ["top", "bottom"]:
                if self.mini_windows[key]:
                    self.mini_windows[key]._save_geometry()
                    self.mini_windows[key].close()
                    self.mini_windows[key] = None
            self.mini_btn.setText("📊 Мини")
    
    def _open_ticker_settings(self):
        dialog = TickerManagerDialog(self)
        dialog.exec()
        
    def _open_mini_settings(self):
        dialog = MiniSettingsDialog(self)
        dialog.exec()
    
    def _make_table(self, show_headers=True):
        t = QTableWidget(0, len(HEADERS))
        if show_headers:
            t.setHorizontalHeaderLabels(HEADERS)
        else:
            t.setHorizontalHeaderLabels([""] * len(HEADERS))
            
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionMode(QTableWidget.NoSelection)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        t.setShowGrid(False)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        t.horizontalHeader().setMinimumSectionSize(30)
        for i, w in enumerate(COL_W): t.setColumnWidth(i, w)
        
        if not show_headers:
            t.horizontalHeader().setVisible(False)
        
        t.cellDoubleClicked.connect(lambda row, col: self._on_cell_double_click(t, row, col))
            
        return t

    def _on_cell_double_click(self, table, row, col):
        if col == 1:  # ИЗМЕНЕНО: тикер теперь в колонке 1
            item = table.item(row, col)
            if item:
                self._copy_ticker(item)

    def _fill_table(self, table, rows, now_ts, batch_flash, dying_keys, show_headers):
        table.setUpdatesEnabled(False)
        table.setRowCount(len(rows))
        
        if not show_headers:
            table.horizontalHeader().setVisible(False)
        else:
            table.horizontalHeader().setVisible(True)

        for r, row in enumerate(rows):
            key = (row["symbol"], row["side"], row["preset"], row["start_ts"])
            base_fg = theme.MUTED if key in dying_keys else (theme.GREEN if row["side"] == "buy" else theme.RED)
            bg = None
            det = batch_flash.get(row["symbol"])
            if det is not None and now_ts - det < 6 and int((now_ts - det) * 3) % 2 == 0: 
                bg = "#2a2a12"
            
            sec = row["seconds_to_next"]
            interval = row["interval"]
            lpp = row.get("price_last")
            sq = row.get("sum_qty")
            st = row.get("start_ts")
            vpm = f"{sq/max((now_ts-st)/60.0,0.1):.0f}" if isinstance(sq,(int,float)) and isinstance(st,(int,float)) else ""
            
            variants = sorted(row["qty_variants"])
            qty_str = f"{variants[0]}-{variants[-1]}" if len(variants) > 1 else str(variants[0])
            
            # CD: обратный отсчёт до удара; просрочка - красным с минусом
            if sec is None:
                cd_str, cd_fg = "-", theme.MUTED
            elif sec >= 0:
                cd_str, cd_fg = f"{sec:.0f}s", theme.TEXT
            else:
                overdue = -sec
                cd_str = f"-{int(overdue // 60)}m" if overdue >= 90 else f"-{int(overdue)}s"
                cd_fg = theme.RED
            
            # NEXT: время следующего удара МИН:СЕК (синхронно с часами)
            if sec is not None:
                next_str = datetime.fromtimestamp(now_ts + sec).strftime("%M:%S")
                next_fg = theme.RED if sec < 0 else theme.YELLOW
            else:
                next_str, next_fg = "-", theme.MUTED
            
            cells = [
                (cd_str, cd_fg),
                (row["symbol"], base_fg), (qty_str, base_fg),
                (f"{interval:.0f}s" if interval else "-", base_fg),
                (next_str, next_fg),
                None,
                (f"{lpp:.3f}" if isinstance(lpp,(int,float)) else "-", theme.TEXT),
                (vpm, theme.TEXT), (str(row["repeats"]), base_fg)
            ]
            
            for c, cell in enumerate(cells):
                if c == 5:  # колонка MS
                    parts = _metro_parts(row)
                    it = QTableWidgetItem(" ".join(p[0] for p in parts))
                    it.setForeground(QColor(parts[-1][1] if parts else theme.TEXT))
                else:
                    txt, fg = cell
                    it = QTableWidgetItem(txt)
                    it.setForeground(QColor(fg))
                
                if bg: it.setBackground(QColor(bg))
                it.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, c, it)
                
        table.setUpdatesEnabled(True)

    def _group_by_ticker(self, rows):
        grouped = defaultdict(list)
        for r in rows:
            grouped[r["symbol"]].append(r)
        
        sorted_tickers = sorted(grouped.keys(), key=lambda t: max(r["repeats"] for r in grouped[t]), reverse=True)
        
        result = []
        for t in sorted_tickers:
            sorted_rows = sorted(grouped[t], key=lambda x: x["repeats"], reverse=True)
            result.extend(sorted_rows)
        return result

    def _init_robots_tab(self, parent):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        
        blocks_lay = QHBoxLayout()
        blocks_lay.setSpacing(4)
        
        self.blocks = []
        for i in range(4):
            block_widget = QWidget()
            block_lay = QVBoxLayout(block_widget)
            block_lay.setContentsMargins(0, 0, 0, 0)
            block_lay.setSpacing(0)
            
            top_table = self._make_table(show_headers=True)
            block_lay.addWidget(top_table)
            
            bottom_table = self._make_table(show_headers=False)
            block_lay.addWidget(bottom_table)
            
            if i < 3:
                separator = QFrame()
                separator.setFrameShape(QFrame.VLine)
                separator.setStyleSheet(f"background: {theme.BORDER}; max-width: 1px;")
                blocks_lay.addWidget(separator)
            
            blocks_lay.addWidget(block_widget, 1)
            self.blocks.append((top_table, bottom_table))
        
        lay.addLayout(blocks_lay)

    def _init_limits_tab(self, parent):
        """Инициализация вкладки Планки"""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        limits_widget = LimitsTab(self.shared_state)
        layout.addWidget(limits_widget)

    def _refresh(self):
        self.setUpdatesEnabled(False)
        now_ts = datetime.now().timestamp()
        self.clock.setText(datetime.now().strftime("%H:%M:%S"))
        
        bf = self.shared_state.batch_flash or {}
        rows = [r for r in self.shared_state.rows if not _is_futures(r["symbol"])]
        
        if self.search_text:
            rows = [r for r in rows if self.search_text in r["symbol"]]
        
        # ИЗМЕНЕНО: просроченные больше не скрываются (показываются красным в CD)
        
        cur = {(r["symbol"], r["side"], r["preset"], r["start_ts"]) for r in rows}
        dk = self._prev_keys - cur
        
        grp_buy = self._group_by_ticker([r for r in rows if r["side"]=="buy" and r["repeats"] >= 2])
        grp_sell = self._group_by_ticker([r for r in rows if r["side"]=="sell" and r["repeats"] >= 2])
        
        long1 = [r for r in grp_buy if r["repeats"] >= CONFIRM_REPEATS]
        long2 = [r for r in grp_buy if 2 <= r["repeats"] < CONFIRM_REPEATS]
        short1 = [r for r in grp_sell if r["repeats"] >= CONFIRM_REPEATS]
        short2 = [r for r in grp_sell if 2 <= r["repeats"] < CONFIRM_REPEATS]
        
        self._fill_table(self.blocks[0][0], long1, now_ts, bf, dk, True)
        self._fill_table(self.blocks[0][1], [], now_ts, bf, dk, False)
        self._fill_table(self.blocks[1][0], long2, now_ts, bf, dk, True)
        self._fill_table(self.blocks[1][1], [], now_ts, bf, dk, False)
        
        self._fill_table(self.blocks[2][0], short1, now_ts, bf, dk, True)
        self._fill_table(self.blocks[2][1], [], now_ts, bf, dk, False)
        
        self._fill_table(self.blocks[3][0], short2, now_ts, bf, dk, True)
        self._fill_table(self.blocks[3][1], [], now_ts, bf, dk, False)
        
        self._prev_keys = cur
        self.setUpdatesEnabled(True)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("Приблуда", "Свёрнуто в трей. Двойной клик для открытия.", 
                                   QSystemTrayIcon.Information, 2000)
        
        if self.mini_windows["top"]:
            self.mini_windows["top"]._save_geometry()
        if self.mini_windows["bottom"]:
            self.mini_windows["bottom"]._save_geometry()