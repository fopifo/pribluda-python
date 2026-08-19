"""
Приблуда на python — управление списком тикеров для детектора.
Добавление, удаление, мьют (неактивные тикеры игнорируются детектором),
настройка параметров (min_qty, min_repeats, интервалы).
"""
import json
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QHeaderView, QCheckBox, QMessageBox, QSpinBox, 
                               QDoubleSpinBox, QAbstractItemView)
from PySide6.QtCore import Qt

from gui import theme

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "ticker_settings.json"

def load_settings():
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class TickerManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управление тикерами (Роботы)")
        self.setFixedSize(900, 700)
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.BG}; color: {theme.TEXT}; }}
            QTableWidget {{ 
                background: {theme.BG}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                gridline-color: {theme.BORDER};
                font-size: 10px;
            }}
            QHeaderView::section {{ 
                background: {theme.PANEL}; 
                color: {theme.MUTED}; 
                border: none; 
                border-bottom: 1px solid {theme.BORDER}; 
                padding: 4px; 
                font-weight: bold; 
                font-size: 10px; 
            }}
            QSpinBox, QDoubleSpinBox {{ 
                background: {theme.PANEL}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                padding: 2px;
                font-size: 10px;
            }}
            QLineEdit {{ 
                background: {theme.PANEL}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                padding: 3px 6px; 
                font-size: 11px; 
                border-radius: 2px;
            }}
            QLineEdit:focus {{ 
                border: 1px solid {theme.GREEN}; 
            }}
            QPushButton {{ 
                background: {theme.PANEL}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                padding: 4px 12px; 
                font-size: 11px; 
                border-radius: 3px;
            }}
            QPushButton:hover {{ 
                background: {theme.BORDER}; 
            }}
            QLabel {{ color: {theme.TEXT}; }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Верхняя панель: добавление
        add_lay = QHBoxLayout()
        add_lay.setSpacing(8)
        
        add_lay.addWidget(QLabel("Новый тикер:"))
        self.new_ticker_input = QLineEdit()
        self.new_ticker_input.setPlaceholderText("Введите тикер (например, SBER)")
        self.new_ticker_input.setFixedWidth(200)
        add_lay.addWidget(self.new_ticker_input)
        
        add_btn = QPushButton("➕ Добавить")
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._add_ticker)
        add_lay.addWidget(add_btn)
        
        add_lay.addStretch()
        
        # Кнопки массовых действий
        enable_all_btn = QPushButton("✅ Включить все")
        enable_all_btn.setFixedWidth(110)
        enable_all_btn.clicked.connect(self._enable_all)
        add_lay.addWidget(enable_all_btn)
        
        disable_all_btn = QPushButton(" Выключить все")
        disable_all_btn.setFixedWidth(120)
        disable_all_btn.clicked.connect(self._disable_all)
        add_lay.addWidget(disable_all_btn)
        
        layout.addLayout(add_lay)
        
        # Таблица тикеров
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Тикер", "В игре", "Min Qty", "Min Repeats", "Min Int (s)", "Max Int (s)", "Действие"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        # УБРАНА ЗЕБРА - все строки одного цвета
        self.table.setAlternatingRowColors(False)
        self.table.setStyleSheet(f"""
            QTableWidget::item:selected {{ background: {theme.BORDER}; }}
            QTableWidget::item {{ background: {theme.BG}; }}
        """)
        layout.addWidget(self.table)
        
        # Подсказка
        hint = QLabel("💡 Снятие галочки 'В игре' полностью отключает детектор для этого тикера (экономия ресурсов). Изменения сохраняются при нажатии 'Сохранить'.")
        hint.setStyleSheet("color: #888; font-size: 10px; padding: 5px;")
        layout.addWidget(hint)
        
        # Кнопки
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(cancel_btn)
        
        save_btn = QPushButton("💾 Сохранить")
        save_btn.setFixedWidth(120)
        save_btn.setStyleSheet(f"background: {theme.GREEN}; color: #000; font-weight: bold;")
        save_btn.clicked.connect(self._save_and_close)
        btn_lay.addWidget(save_btn)
        
        layout.addLayout(btn_lay)
        
        self._load_data()

    def _load_data(self):
        self.table.setRowCount(0)
        data = load_settings()
        
        for ticker, settings in sorted(data.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Тикер (не редактируется)
            ticker_item = QTableWidgetItem(ticker)
            ticker_item.setFlags(ticker_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, ticker_item)
            
            # Чекбокс Active
            is_active = settings.get("active", True)
            cb = QCheckBox()
            cb.setChecked(is_active)
            cb.setStyleSheet("margin-left: 50%;")
            self.table.setCellWidget(row, 1, cb)
            
            # Min Qty
            min_qty_spin = QSpinBox()
            min_qty_spin.setRange(1, 10000)
            min_qty_spin.setValue(settings.get("min_qty", 10))
            min_qty_spin.setFixedWidth(70)
            self.table.setCellWidget(row, 2, min_qty_spin)
            
            # Min Repeats
            min_repeats_spin = QSpinBox()
            min_repeats_spin.setRange(2, 20)
            min_repeats_spin.setValue(settings.get("min_repeats", 3))
            min_repeats_spin.setFixedWidth(70)
            self.table.setCellWidget(row, 3, min_repeats_spin)
            
            # Min Interval
            min_int_spin = QDoubleSpinBox()
            min_int_spin.setRange(0.1, 3600.0)
            min_int_spin.setSingleStep(0.5)
            min_int_spin.setDecimals(1)
            min_int_spin.setValue(settings.get("min_interval", 2.0))
            min_int_spin.setFixedWidth(80)
            self.table.setCellWidget(row, 4, min_int_spin)
            
            # Max Interval
            max_int_spin = QDoubleSpinBox()
            max_int_spin.setRange(1.0, 7200.0)
            max_int_spin.setSingleStep(10.0)
            max_int_spin.setDecimals(0)
            max_int_spin.setValue(settings.get("max_interval", 600.0))
            max_int_spin.setFixedWidth(80)
            self.table.setCellWidget(row, 5, max_int_spin)
            
            # Кнопка удаления
            del_btn = QPushButton("🗑 Удалить")
            del_btn.setFixedWidth(90)
            del_btn.setStyleSheet("background: #3a1a1a; color: #ff4444;")
            del_btn.clicked.connect(lambda checked, r=row: self._remove_ticker(r))
            self.table.setCellWidget(row, 6, del_btn)

    def _add_ticker(self):
        ticker = self.new_ticker_input.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Ошибка", "Введите название тикера.")
            return
            
        data = load_settings()
        if ticker in data:
            QMessageBox.warning(self, "Ошибка", f"Тикер {ticker} уже существует.")
            return
            
        # Дефолтные настройки для нового тикера
        data[ticker] = {
            "active": True,
            "min_qty": 10,
            "min_repeats": 3,
            "min_interval": 2.0,
            "max_interval": 600.0,
            "interval_tolerance": 0.1
        }
        save_settings(data)
        self.new_ticker_input.clear()
        self._load_data()

    def _remove_ticker(self, row):
        ticker = self.table.item(row, 0).text()
        reply = QMessageBox.question(self, "Подтверждение", 
                                     f"Удалить тикер {ticker} из отслеживания?", 
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            data = load_settings()
            if ticker in data:
                del data[ticker]
                save_settings(data)
                self._load_data()

    def _enable_all(self):
        """Включить все тикеры"""
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, 1)
            if cb:
                cb.setChecked(True)

    def _disable_all(self):
        """Выключить все тикеры"""
        reply = QMessageBox.question(self, "Подтверждение", 
                                     "Выключить ВСЕ тикеры? Детектор перестанет работать.", 
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            for row in range(self.table.rowCount()):
                cb = self.table.cellWidget(row, 1)
                if cb:
                    cb.setChecked(False)

    def _save_and_close(self):
        # Сохраняем состояние всех виджетов
        data = load_settings()
        for row in range(self.table.rowCount()):
            ticker = self.table.item(row, 0).text()
            if ticker not in data:
                continue
                
            # Чекбокс Active
            cb = self.table.cellWidget(row, 1)
            data[ticker]["active"] = cb.isChecked() if cb else True
            
            # Min Qty
            min_qty_spin = self.table.cellWidget(row, 2)
            if min_qty_spin:
                data[ticker]["min_qty"] = min_qty_spin.value()
            
            # Min Repeats
            min_repeats_spin = self.table.cellWidget(row, 3)
            if min_repeats_spin:
                data[ticker]["min_repeats"] = min_repeats_spin.value()
            
            # Min Interval
            min_int_spin = self.table.cellWidget(row, 4)
            if min_int_spin:
                data[ticker]["min_interval"] = min_int_spin.value()
            
            # Max Interval
            max_int_spin = self.table.cellWidget(row, 5)
            if max_int_spin:
                data[ticker]["max_interval"] = max_int_spin.value()
        
        save_settings(data)
        QMessageBox.information(self, "Сохранено", f"Настройки {len(data)} тикеров сохранены.")
        self.accept()