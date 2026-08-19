"""
Приблуда на python — мини-окна для стаканов (PySide6).
Поддержка нескольких тикеров в одной ячейке (через запятую или пробел).
Автосохранение геометрии (включая позицию на другом мониторе).
"""
import json
import re
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QFont, QCursor
from PySide6.QtWidgets import QWidget, QGridLayout, QLabel, QVBoxLayout

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "mini_window_config.json"

RESIZE_MARGIN = 8

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"top_row": [None]*10, "bottom_row": [None]*10}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=2, ensure_ascii=False)

def parse_tickers(text):
    """Парсинг тикеров через запятую или пробел"""
    if not text:
        return []
    tickers = re.split(r'[,\s]+', text)
    return [t.strip().upper() for t in tickers if t.strip()]


class MiniCell(QLabel):
    """Ячейка с поддержкой нескольких тикеров"""
    def __init__(self, tickers_str, shared_state):
        super().__init__()
        self.tickers_str = tickers_str
        self.shared_state = shared_state
        self.setAlignment(Qt.AlignCenter)
        self.update_content()

    def _parse_tickers(self):
        """Парсинг строки тикеров в список"""
        return parse_tickers(self.tickers_str)

    def update_content(self):
        tickers = self._parse_tickers()
        if not tickers:
            self.setText("")
            self.setStyleSheet("background: #0a0a1a; border: 1px solid #1a1a2e;")
            return
        
        rows = self.shared_state.rows or []
        
        # Собираем роботов для всех тикеров
        all_robots = []
        for ticker in tickers:
            ticker_rows = [r for r in rows if r["symbol"] == ticker]
            if ticker_rows:
                best = max(ticker_rows, key=lambda x: x.get("repeats", 0))
                all_robots.append((ticker, best))
        
        if not all_robots:
            # Нет роботов - показываем только тикеры серым
            tickers_display = ', '.join(tickers)
            self.setText(f"<span style='color:#555'>{tickers_display}</span>")
            self.setStyleSheet("background: #1a1a2e; border: 1px solid #2a2a4a;")
            return
        
        # Формируем HTML с роботами
        html_parts = []
        for ticker, best in all_robots:
            sec = best.get("seconds_to_next")
            next_str = f"{sec:.0f}s" if sec else "-"
            qty = "-".join(str(q) for q in best.get("qty_variants", []))
            interval = best.get("interval")
            int_str = f"{interval:.0f}s" if interval else "-"
            rep = best.get("repeats", 0)
            side = best.get("side", "buy")
            
            color = "#00ff00" if side == "buy" else "#ff4444"
            
            html_parts.append(
                f"<div style='color: {color}; font-weight: bold;'>"
                f"{next_str} <span style='color: white;'>{ticker}</span> {qty} {int_str} x{rep}"
                f"</div>"
            )
        
        html = '\n'.join(html_parts)
        self.setText(html)
        
        # Цвет рамки - если есть роботы, используем цвет первого
        first_color = "#00ff00" if all_robots[0][1].get("side", "buy") == "buy" else "#ff4444"
        self.setStyleSheet(f"background: #1a1a2e; border: 1px solid {first_color}; border-radius: 2px;")


class MiniWindow(QWidget):
    def __init__(self, shared_state, row_type="top"):
        super().__init__()
        self.shared_state = shared_state
        self.row_type = row_type
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet("background: #0a0a1a;")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        self.layout.addLayout(self.grid)
        
        self.cells = []
        self._build_cells()
        
        # Загрузка геометрии
        config = load_config()
        geo = config.get(f"{row_type}_geometry")
        if geo:
            self.resize(geo.get("w", 1200), geo.get("h", 60))
            self.move(geo.get("x", 100), geo.get("y", 100))
        else:
            self.resize(1200, 60)
            if row_type == "top": self.move(100, 100)
            else: self.move(100, 200)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_all)
        self.timer.start(1000)
        
        self._drag_pos = None
        self._resize_edge = None
        self._resize_start_geo = None

    def _build_cells(self):
        config = load_config()
        tickers_list = config.get(f"{self.row_type}_row", [None]*10)
        for i in range(10):
            tickers_str = tickers_list[i] if i < len(tickers_list) else None
            cell = MiniCell(tickers_str, self.shared_state)
            self.grid.addWidget(cell, 0, i)
            self.cells.append(cell)

    def _update_all(self):
        config = load_config()
        tickers_list = config.get(f"{self.row_type}_row", [None]*10)
        for i, cell in enumerate(self.cells):
            new_tickers_str = tickers_list[i] if i < len(tickers_list) else None
            if cell.tickers_str != new_tickers_str:
                cell.tickers_str = new_tickers_str
            cell.update_content()

    def _save_geometry(self):
        config = load_config()
        config[f"{self.row_type}_geometry"] = {
            "x": self.x(),
            "y": self.y(),
            "w": self.width(),
            "h": self.height()
        }
        save_config(config)

    def _get_edge(self, pos):
        rect = self.rect()
        left = pos.x() < RESIZE_MARGIN
        right = pos.x() > rect.width() - RESIZE_MARGIN
        top = pos.y() < RESIZE_MARGIN
        bottom = pos.y() > rect.height() - RESIZE_MARGIN

        if top and left: return Qt.TopLeftCorner
        if top and right: return Qt.TopRightCorner
        if bottom and left: return Qt.BottomLeftCorner
        if bottom and right: return Qt.BottomRightCorner
        if left: return Qt.LeftEdge
        if right: return Qt.RightEdge
        if top: return Qt.TopEdge
        if bottom: return Qt.BottomEdge
        return None

    def _get_cursor_for_edge(self, edge):
        if edge in (Qt.TopLeftCorner, Qt.BottomRightCorner): return QCursor(Qt.SizeFDiagCursor)
        if edge in (Qt.TopRightCorner, Qt.BottomLeftCorner): return QCursor(Qt.SizeBDiagCursor)
        if edge in (Qt.LeftEdge, Qt.RightEdge): return QCursor(Qt.SizeHorCursor)
        if edge in (Qt.TopEdge, Qt.BottomEdge): return QCursor(Qt.SizeVerCursor)
        return QCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._resize_edge = self._get_edge(event.position().toPoint())
            if self._resize_edge:
                self._resize_start_geo = QRect(self.x(), self.y(), self.width(), self.height())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.NoButton:
            edge = self._get_edge(event.position().toPoint())
            self.setCursor(self._get_cursor_for_edge(edge))
            return

        if event.buttons() == Qt.LeftButton:
            if self._resize_edge:
                global_pos = event.globalPosition().toPoint()
                start_pos = self._resize_start_geo.topLeft()
                delta = global_pos - (start_pos + self._drag_pos)
                
                new_x = self._resize_start_geo.x()
                new_y = self._resize_start_geo.y()
                new_w = self._resize_start_geo.width()
                new_h = self._resize_start_geo.height()
                
                if self._resize_edge in (Qt.LeftEdge, Qt.TopLeftCorner, Qt.BottomLeftCorner):
                    new_x += delta.x()
                    new_w -= delta.x()
                if self._resize_edge in (Qt.RightEdge, Qt.TopRightCorner, Qt.BottomRightCorner):
                    new_w += delta.x()
                if self._resize_edge in (Qt.TopEdge, Qt.TopLeftCorner, Qt.TopRightCorner):
                    new_y += delta.y()
                    new_h -= delta.y()
                if self._resize_edge in (Qt.BottomEdge, Qt.BottomLeftCorner, Qt.BottomRightCorner):
                    new_h += delta.y()
                
                if new_w < 400: new_w = 400
                if new_h < 30: new_h = 30
                
                self.setGeometry(new_x, new_y, new_w, new_h)
            else:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._resize_edge = None
            self._drag_pos = None
            self._save_geometry()
            event.accept()

    def moveEvent(self, event):
        super().moveEvent(event)
        if not self._drag_pos:
            self._save_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = event.size().height()
        font_size = max(8, height // 2.5)
        font = QFont("Segoe UI", font_size, QFont.Bold)
        for cell in self.cells:
            cell.setFont(font)
        if not self._resize_edge:
            self._save_geometry()

    def closeEvent(self, event):
        self._save_geometry()
        super().closeEvent(event)