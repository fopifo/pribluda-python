"""
Приблуда на python — диалог настройки тикеров для мини-окон.
Поддержка нескольких тикеров в одной ячейке (через запятую или пробел).
Транслитерация русской раскладки.
Тёмная тема, минималистичный дизайн.
"""
import json
import re
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QGridLayout, QGroupBox,
                               QScrollArea, QWidget)
from PySide6.QtCore import Qt

from gui import theme

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "mini_window_config.json"

# Карта транслитерации
TRANS_MAP = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
    'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
})

def transliterate(text):
    return text.lower().translate(TRANS_MAP)

def parse_tickers(text):
    """Парсинг тикеров через запятую или пробел"""
    if not text:
        return []
    tickers = re.split(r'[,\s]+', text)
    return [t.strip().upper() for t in tickers if t.strip()]

def format_tickers(tickers_list):
    """Форматирование списка тикеров для отображения в поле ввода"""
    return ', '.join(tickers_list)

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"top_row": [None]*10, "bottom_row": [None]*10}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=2, ensure_ascii=False)


class MiniSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки мини-окон")
        self.setFixedSize(500, 650)
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.BG}; color: {theme.TEXT}; }}
            QGroupBox {{ 
                border: 1px solid {theme.BORDER}; 
                border-radius: 5px; 
                margin-top: 10px; 
                padding-top: 10px; 
                background: {theme.PANEL};
            }}
            QGroupBox::title {{ 
                subcontrol-origin: margin; 
                left: 10px; 
                padding: 0 5px; 
                color: {theme.MUTED};
            }}
            QLineEdit {{ 
                background: {theme.BG}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                border-radius: 2px; 
                padding: 3px 6px; 
                font-size: 10px;
            }}
            QLineEdit:focus {{ 
                border: 1px solid {theme.GREEN}; 
            }}
            QPushButton {{ 
                background: {theme.PANEL}; 
                color: {theme.TEXT}; 
                border: 1px solid {theme.BORDER}; 
                padding: 5px 15px; 
                font-size: 11px; 
                border-radius: 3px;
            }}
            QPushButton:hover {{ 
                background: {theme.BORDER}; 
            }}
            QLabel {{ color: {theme.MUTED}; font-size: 10px; }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Скролл для контента
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"border: none; background: {theme.BG};")
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(15)
        
        self.inputs = {"top": [], "bottom": []}
        
        for row_name, title in [("top", "Верхний ряд стаканов"), ("bottom", "Нижний ряд стаканов")]:
            group = QGroupBox(title)
            grid = QGridLayout()
            grid.setSpacing(8)
            
            config = load_config()
            current_tickers = config.get(f"{row_name}_row", [None]*10)
            
            for i in range(10):
                label = QLabel(f"{i+1}:")
                label.setFixedWidth(25)
                label.setStyleSheet("color: #888;")
                
                line = QLineEdit(current_tickers[i] or "")
                line.setPlaceholderText("SBER GAZP или SBER,GAZP")
                line.textChanged.connect(lambda text, ln=line: self._handle_input(ln, text))
                grid.addWidget(label, i, 0)
                grid.addWidget(line, i, 1)
                self.inputs[row_name].append(line)
            
            group.setLayout(grid)
            content_layout.addWidget(group)
        
        # Подсказка
        hint = QLabel("Разделители: запятая или пробел. Пример: SBER GAZP LKOH или SBER, GAZP, LKOH")
        hint.setStyleSheet("color: #666; font-size: 9px; padding: 5px;")
        content_layout.addWidget(hint)
        
        content_layout.addStretch()
        
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("Сохранить")
        save_btn.setFixedWidth(100)
        save_btn.clicked.connect(self._save_and_close)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def _handle_input(self, line_edit, text):
        if any(c in TRANS_MAP for c in text.lower()):
            tickers = parse_tickers(text)
            new_tickers = []
            for t in tickers:
                if any(c in TRANS_MAP for c in t.lower()):
                    new_tickers.append(transliterate(t).upper())
                else:
                    new_tickers.append(t.upper())
            new_text = ', '.join(new_tickers)
            line_edit.blockSignals(True)
            line_edit.setText(new_text)
            line_edit.blockSignals(False)
            line_edit.setCursorPosition(len(new_text))

    def _save_and_close(self):
        config = {
            "top_row": [ln.text().strip() for ln in self.inputs["top"]],
            "bottom_row": [ln.text().strip() for ln in self.inputs["bottom"]]
        }
        save_config(config)
        self.accept()