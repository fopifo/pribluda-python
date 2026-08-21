"""
Приблуда на python — вкладка "Планки" (v4).
v4: ИСПРАВЛЕНО BASE_UI_FILE — файл в gui/tabs/limits/, до корня 4 уровня
(.parent x4); было 3 → limits_ui.json создавался в gui/ вместо корня.
Теперь настройки зоны лежат в корне рядом с ui_settings.json и др.
- Колонка "ДО ПЛАНКИ" со стрелкой и цветом, "ПОЗИЦИЯ ДНЯ" мини-полосой,
статусы из Quik (аукцион/приостановка).
- Регулируемая зона (спинбокс), фильтр "только интересные",
сортировка по близости к планке.
- УВЕДОМЛЕНИЕ В МОМЕНТ входа в зону: звук (двойной бип, учитывает 🔊),
мигание строки здесь, мигание тикера на главном экране и чипы в верхней
панели (через shared_state.limit_alerts, читает main_window).
- Гистерезис (вход <= зона, выход > зона+0.5) и кулдаун звука 10 минут.
- v3 (производительность): скрытая вкладка не пересчитывает таблицы;
блок "ОСТАЛЬНЫЕ" ограничен 150 строками; обновление раз в 3 c.
Архитектура: gui/tabs/limits/. Не торгует, только чтение.
"""
import json
import threading
import time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)
from connectors.quik.limits_reader import LimitsReader
from connectors.quik.quotes_reader import QuotesReader
from core.sound_manager import SoundManager
from gui import theme

# gui/tabs/limits/limits_tab.py -> корень: 4 уровня вверх
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
BASE_UI_FILE = BASE_DIR / "limits_ui.json"

SOUND_COOLDOWN_SEC = 600.0   # повторный бип по тикеру не чаще раза в 10 минут
FLASH_SEC = 60.0             # мигание после входа в зону
REST_MAX_ROWS = 150          # ограничение блока "ОСТАЛЬНЫЕ" (производительность)


def _load_ui():
    try:
        return json.loads(BASE_UI_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_ui(data):
    try:
        BASE_UI_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except (OSError, TypeError):
        pass


def _bar(pos):
    if pos is None:
        return "-"
    k = int(round(pos / 10))
    return "█" * k + "░" * (10 - k) + f" {pos:.0f}%"


def _beep():
    def run():
        try:
            import winsound
            winsound.Beep(1200, 120)
            time.sleep(0.06)
            winsound.Beep(1200, 120)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


class LimitsTab(QWidget):
    def __init__(self, shared_state):
        super().__init__()
        self.shared_state = shared_state
        self.limits_reader = LimitsReader()
        self.quotes_reader = QuotesReader()
        ui = _load_ui()
        self._known = {}      # тикер -> ts первого входа в зону (для мигания/кулдауна)
        self._last_sound = {}  # тикер -> ts последнего бипа

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        top = QHBoxLayout()
        header = QLabel("📏 ЦЕНОВЫЕ ПЛАНКИ TQBR")
        header.setStyleSheet(f"color: {theme.TEXT}; font-weight: bold; padding: 2px;")
        top.addWidget(header)
        top.addWidget(QLabel("зона:"))
        self.zone_sb = QDoubleSpinBox()
        self.zone_sb.setRange(0.1, 10.0)
        self.zone_sb.setSingleStep(0.1)
        self.zone_sb.setDecimals(1)
        self.zone_sb.setValue(float(ui.get("zone", 1.5)))
        self.zone_sb.setFixedWidth(60)
        self.zone_sb.valueChanged.connect(self._on_ui_changed)
        top.addWidget(self.zone_sb)
        top.addWidget(QLabel("%"))
        self.only_cb = QCheckBox("только интересные")
        self.only_cb.setChecked(bool(ui.get("only_interesting", False)))
        self.only_cb.toggled.connect(self._on_ui_changed)
        top.addWidget(self.only_cb)
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        top.addWidget(self.info_lbl, 1)
        layout.addLayout(top)

        blocks_layout = QHBoxLayout()
        blocks_layout.setSpacing(4)
        self.block_zone = self._create_block("🚨 В ЗОНЕ (планка рядом)", theme.RED)
        blocks_layout.addWidget(self.block_zone, 1)
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFixedWidth(1)
        sep1.setStyleSheet(f"background: {theme.BORDER}; border: none;")
        blocks_layout.addWidget(sep1)
        self.block_near = self._create_block("РЯДОМ (до 5%)", theme.YELLOW)
        blocks_layout.addWidget(self.block_near, 1)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background: {theme.BORDER}; border: none;")
        blocks_layout.addWidget(sep2)
        self.block_rest = self._create_block("ОСТАЛЬНЫЕ", theme.MUTED)
        blocks_layout.addWidget(self.block_rest, 1)
        layout.addLayout(blocks_layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(3000)
        self._refresh()

    def _on_ui_changed(self, _=None):
        _save_ui({"zone": float(self.zone_sb.value()),
                  "only_interesting": bool(self.only_cb.isChecked())})

    def _create_block(self, title, color):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {color}; font-weight: bold; "
                                  f"font-size: 10px; padding: 2px; background: transparent;")
        layout.addWidget(title_label)
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(
            ["ТИКЕР", "ЦЕНА", "ДО ПЛАНКИ", "ПОЗИЦИЯ ДНЯ", "ИЗМ.%", "СТАТУС"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(16)
        table.setStyleSheet(f"""
            QTableWidget {{ background: {theme.BG}; border: none; font-size: 9px; }}
            QHeaderView::section {{ background: {theme.PANEL}; color: {theme.MUTED};
                border: none; border-bottom: 1px solid {theme.BORDER};
                padding: 2px; font-size: 8px; }}
            QTableWidget::item {{ border: none; padding: 1px; }}
        """)
        layout.addWidget(table)
        return container

    def _status_of(self, q):
        if not q:
            return "", theme.MUTED
        if q.get("openperiod") == 1:
            return "АУКЦИОН", theme.YELLOW
        st = q.get("tradingstatus")
        if st is not None and st != 1:
            return "приост.", theme.RED
        return "", theme.MUTED

    def _fill_table(self, table, items, now_ts):
        zone = float(self.zone_sb.value())
        table.setRowCount(len(items))
        for r, l in enumerate(items):
            direction, dist = l.nearest()
            arrow = "↑" if direction == "up" else "↓"
            if dist <= zone:
                dist_fg = theme.RED
            elif dist <= 3.0:
                dist_fg = theme.YELLOW
            else:
                dist_fg = theme.TEXT
            status, st_fg = self._status_of(self.quotes.get(l.ticker))
            first = self._known.get(l.ticker)
            flash = (first is not None and now_ts - first <= FLASH_SEC
                     and int(now_ts * 3) % 2 == 0)
            cells = [
                (l.ticker, theme.RED if dist <= zone else theme.TEXT),
                (f"{l.current_price:.2f}", theme.TEXT),
                (f"{arrow}{dist:.2f}%", dist_fg),
                (_bar(l.day_position()), theme.MUTED),
                (f"{l.change_percent:+.2f}", theme.GREEN if l.change_percent >= 0 else theme.RED),
                (status, st_fg),
            ]
            for c, (txt, fg) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setForeground(QColor(fg))
                if flash:
                    it.setBackground(QColor("#3a1a1a"))
                table.setItem(r, c, it)

    def _refresh(self):
        # v3: скрытая вкладка спит — не тратим CPU, пока смотрят другие вкладки
        if not self.isVisible():
            return
        try:
            now_ts = time.time()
            zone = float(self.zone_sb.value())
            limits = self.limits_reader.read()
            self.quotes = self.quotes_reader.read()
            self.info_lbl.setText(f"инструментов: {len(limits)}")

            # --- уведомления: вход/выход из зоны с гистерезисом ---
            alerts = []
            for l in limits.values():
                direction, dist = l.nearest()
                if dist <= zone:
                    first = self._known.get(l.ticker)
                    if first is None:
                        first = now_ts
                        last = self._last_sound.get(l.ticker, 0.0)
                        try:
                            enabled = SoundManager().enabled
                        except Exception:
                            enabled = True
                        if enabled and now_ts - last >= SOUND_COOLDOWN_SEC:
                            self._last_sound[l.ticker] = now_ts
                            _beep()
                    self._known[l.ticker] = first
                    alerts.append({"ticker": l.ticker, "direction": direction,
                                   "distance": dist, "ts_first": first})
                elif l.ticker in self._known and dist > zone + 0.5:
                    self._known.pop(l.ticker, None)
            alerts.sort(key=lambda a: a["distance"])
            try:
                self.shared_state.limit_alerts = alerts
            except Exception:
                pass

            # --- блоки ---
            in_zone = [l for l in limits.values()
                       if min(l.distance_to_up, l.distance_to_down) <= zone]
            near = [l for l in limits.values()
                    if zone < min(l.distance_to_up, l.distance_to_down) <= 5.0]
            rest = [l for l in limits.values()
                    if min(l.distance_to_up, l.distance_to_down) > 5.0]
            if self.only_cb.isChecked():
                rest = [l for l in rest
                        if abs(l.change_percent) >= 1.0
                        or (self.quotes.get(l.ticker) or {}).get("openperiod") == 1]
            in_zone.sort(key=lambda l: min(l.distance_to_up, l.distance_to_down))
            near.sort(key=lambda l: min(l.distance_to_up, l.distance_to_down))
            rest.sort(key=lambda l: min(l.distance_to_up, l.distance_to_down))
            rest = rest[:REST_MAX_ROWS]  # v3: не рисуем 800 строк — тормозит
            self._fill_table(self.block_zone.layout().itemAt(1).widget(), in_zone, now_ts)
            self._fill_table(self.block_near.layout().itemAt(1).widget(), near, now_ts)
            self._fill_table(self.block_rest.layout().itemAt(1).widget(), rest, now_ts)
        except Exception as e:
            self.info_lbl.setText(f"Ошибка: {type(e).__name__}: {e}")