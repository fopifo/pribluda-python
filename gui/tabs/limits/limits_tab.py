"""Вкладка Планки"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, 
                               QTableWidget, QTableWidgetItem, QHeaderView, 
                               QWidget, QFrame)

from gui import theme
from connectors.quik.limits_reader import LimitsReader


class LimitsTab(QWidget):
    def __init__(self, shared_state):
        super().__init__()
        self.shared_state = shared_state
        self.limits_reader = LimitsReader()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        
        header = QLabel("📏 ЦЕНОВЫЕ ПЛАНКИ TQBR")
        header.setFont(QFont(theme.FONT_FAMILY, 12, QFont.Bold))
        header.setStyleSheet(f"color: {theme.TEXT}; padding: 3px;")
        layout.addWidget(header)
        
        # 3 блока
        blocks_layout = QHBoxLayout()
        blocks_layout.setSpacing(4)
        
        self.block_at = self._create_block("В ПЛАНКЕ (<0.1%)", theme.RED)
        blocks_layout.addWidget(self.block_at, 1)
        
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setStyleSheet(f"background: {theme.BORDER}; max-width: 1px;")
        blocks_layout.addWidget(sep1)
        
        self.block_1pct = self._create_block("1% ДО ПЛАНКИ", theme.YELLOW)
        blocks_layout.addWidget(self.block_1pct, 1)
        
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet(f"background: {theme.BORDER}; max-width: 1px;")
        blocks_layout.addWidget(sep2)
        
        self.block_5pct = self._create_block("5% ДО ПЛАНКИ", theme.GREEN)
        blocks_layout.addWidget(self.block_5pct, 1)
        
        layout.addLayout(blocks_layout)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(3000)
        
        self._refresh()
    
    def _create_block(self, title, color):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        title_label = QLabel(title)
        title_label.setFont(QFont(theme.FONT_FAMILY, 10, QFont.Bold))
        title_label.setStyleSheet(f"color: {color}; padding: 2px;")
        layout.addWidget(title_label)
        
        upper_label = QLabel("ВЕРХНЯЯ ▲")
        upper_label.setFont(QFont(theme.FONT_FAMILY, 8))
        upper_label.setStyleSheet(f"color: {theme.GREEN}; padding: 1px;")
        layout.addWidget(upper_label)
        
        self.upper_table = self._create_table()
        layout.addWidget(self.upper_table)
        
        lower_label = QLabel("НИЖНЯЯ ▼")
        lower_label.setFont(QFont(theme.FONT_FAMILY, 8))
        lower_label.setStyleSheet(f"color: {theme.RED}; padding: 1px;")
        layout.addWidget(lower_label)
        
        self.lower_table = self._create_table()
        layout.addWidget(self.lower_table)
        
        return container
    
    def _create_table(self):
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["ТИКЕР", "ЦЕНА", "ПЛАНКА", "%"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(16)
        table.setStyleSheet(f"""
            QTableWidget {{ background: {theme.BG}; border: none; font-size: 9px; }}
            QHeaderView::section {{ background: {theme.PANEL}; color: {theme.MUTED}; 
                border: none; border-bottom: 1px solid {theme.BORDER}; padding: 2px; font-size: 8px; }}
            QTableWidget::item {{ border: none; padding: 1px; }}
        """)
        table.cellDoubleClicked.connect(lambda r, c: self._copy_ticker(table, r, c))
        return table
    
    def _copy_ticker(self, table, row, col):
        if col == 0:
            item = table.item(row, col)
            if item:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(item.text())
    
    def _refresh(self):
        limits = self.limits_reader.get_near_limits(5.0)
        
        # Категории
        at_limits = [l for l in limits if l.distance_to_up < 0.1 or l.distance_to_down < 0.1]
        near_1pct = [l for l in limits if 0.1 <= l.distance_to_up < 1.0 or 0.1 <= l.distance_to_down < 1.0]
        near_5pct = [l for l in limits if 1.0 <= l.distance_to_up <= 5.0 or 1.0 <= l.distance_to_down <= 5.0]
        
        self._fill_block(self.block_at, at_limits)
        self._fill_block(self.block_1pct, near_1pct)
        self._fill_block(self.block_5pct, near_5pct)
    
    def _fill_block(self, block, limits):
        tables = block.findChildren(QTableWidget)
        if len(tables) < 2:
            return
        
        upper_table = tables[0]
        lower_table = tables[1]
        
        upper_limits = [l for l in limits if l.distance_to_up <= 5.0]
        lower_limits = [l for l in limits if l.distance_to_down <= 5.0]
        
        self._fill_table(upper_table, upper_limits, theme.GREEN, "up")
        self._fill_table(lower_table, lower_limits, theme.RED, "down")
    
    def _fill_table(self, table, limits, color, direction):
        table.setRowCount(len(limits))
        
        for row, limit in enumerate(limits):
            if direction == "up":
                price = limit.limit_up
                distance = limit.distance_to_up
            else:
                price = limit.limit_down
                distance = limit.distance_to_down
            
            items = [
                QTableWidgetItem(limit.ticker),
                QTableWidgetItem(f"{limit.current_price:.2f}"),
                QTableWidgetItem(f"{price:.2f}"),
                QTableWidgetItem(f"{distance:.1f}%")
            ]
            
            for col, item in enumerate(items):
                if col == 0:
                    item.setForeground(QColor(color))
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)