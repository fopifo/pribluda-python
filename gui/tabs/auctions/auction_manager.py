"""
Приблуда на python — диалог настроек вкладки "Аукционы" (v2.1):
группы (эшелоны), тикеры "в игре", мьют, добавление/удаление/перенос.
v2.1: фикс NameError QColor (З-001).
"＋ Все (MOEX)" тянет полный список акций TQBR с MOEX ISS.
Пишет auction_settings.json через core/auction_settings.
Архитектура: gui/tabs/auctions/.
"""
import threading

import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from core import auction_settings as cfg_mod
from gui import theme

ISS_TQBR_URL = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
                "boards/TQBR/securities.json?iss.meta=off")


class AuctionManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Аукционы: настройки тикеров и групп")
        self.resize(760, 500)
        self.cfg = cfg_mod.load_auction_settings()

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Группы (эшелоны):"))
        self.groups_list = QListWidget()
        self.groups_list.currentRowChanged.connect(self._fill_tickers)
        left.addWidget(self.groups_list, 1)
        grp_row = QHBoxLayout()
        self.new_group_ed = QLineEdit()
        self.new_group_ed.setPlaceholderText("Новая группа...")
        grp_row.addWidget(self.new_group_ed, 1)
        add_grp_btn = QPushButton("+ Группа")
        add_grp_btn.clicked.connect(self._add_group)
        grp_row.addWidget(add_grp_btn)
        del_grp_btn = QPushButton("− Группа")
        del_grp_btn.clicked.connect(self._del_group)
        grp_row.addWidget(del_grp_btn)
        left.addLayout(grp_row)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Тикеры выбранной группы:"))
        self.tick_table = QTableWidget(0, 3)
        self.tick_table.setHorizontalHeaderLabels(["ТИКЕР", "МЬЮТ", "УДАЛИТЬ"])
        self.tick_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tick_table.verticalHeader().setVisible(False)
        self.tick_table.setColumnWidth(0, 120)
        self.tick_table.setColumnWidth(1, 60)
        right.addWidget(self.tick_table, 1)

        move_row = QHBoxLayout()
        move_row.addWidget(QLabel("Перенести выбранные в:"))
        self.move_cb = QComboBox()
        move_row.addWidget(self.move_cb, 1)
        move_btn = QPushButton("Перенести")
        move_btn.clicked.connect(self._move_selected)
        move_row.addWidget(move_btn)
        right.addLayout(move_row)

        add_row = QHBoxLayout()
        self.new_tick_ed = QLineEdit()
        self.new_tick_ed.setPlaceholderText("Тикер (например, SBER)")
        add_row.addWidget(self.new_tick_ed, 1)
        self.add_cb = QComboBox()
        add_row.addWidget(self.add_cb, 1)
        add_btn = QPushButton("+ Тикер")
        add_btn.clicked.connect(self._add_ticker)
        add_row.addWidget(add_btn)
        self.add_all_btn = QPushButton("＋ Все (MOEX)")
        self.add_all_btn.setToolTip("Добавить ВСЕ акции TQBR с Мосбиржи "
                                    "в выбранную группу")
        self.add_all_btn.clicked.connect(self._add_all_from_moex)
        add_row.addWidget(self.add_all_btn)
        right.addLayout(add_row)

        root.addLayout(right, 2)
        self._refresh_groups()

    def _save(self):
        try:
            cfg_mod.save_auction_settings(self.cfg)
        except Exception as e:
            self.setWindowTitle(f"Ошибка сохранения: {e}")

    def _refresh_groups(self):
        cur = self.groups_list.currentRow()
        self.groups_list.blockSignals(True)
        self.groups_list.clear()
        for g in self.cfg["groups"]:
            self.groups_list.addItem(f"{g['name']}  ({len(g['tickers'])})")
        if 0 <= cur < self.groups_list.count():
            self.groups_list.setCurrentRow(cur)
        elif self.groups_list.count():
            self.groups_list.setCurrentRow(0)
        self.groups_list.blockSignals(False)
        self.move_cb.clear()
        self.add_cb.clear()
        for g in self.cfg["groups"]:
            self.move_cb.addItem(g["name"])
            self.add_cb.addItem(g["name"])
        self._fill_tickers(self.groups_list.currentRow())

    def _current_group(self):
        i = self.groups_list.currentRow()
        if 0 <= i < len(self.cfg["groups"]):
            return self.cfg["groups"][i]
        return None

    def _fill_tickers(self, row):
        self.tick_table.setRowCount(0)
        g = self._current_group()
        if g is None:
            return
        for t in g["tickers"]:
            r = self.tick_table.rowCount()
            self.tick_table.insertRow(r)
            it = QTableWidgetItem(t)
            it.setForeground(QColor(theme.MUTED if t in self.cfg["muted"] else theme.TEXT))
            self.tick_table.setItem(r, 0, it)
            cb = QCheckBox()
            cb.setChecked(t in self.cfg["muted"])
            cb.stateChanged.connect(
                lambda state, tk=t: self._toggle_mute(tk, state))
            self.tick_table.setCellWidget(r, 1, cb)
            del_btn = QPushButton("🗑")
            del_btn.setFixedWidth(40)
            del_btn.clicked.connect(
                lambda checked, tk=t: self._remove_ticker(tk))
            self.tick_table.setCellWidget(r, 2, del_btn)

    def _toggle_mute(self, ticker, state):
        checked = (state == Qt.CheckState.Checked.value) if hasattr(
            Qt.CheckState.Checked, "value") else (state == 2)
        muted = set(self.cfg["muted"])
        if checked:
            muted.add(ticker)
        else:
            muted.discard(ticker)
        self.cfg["muted"] = sorted(muted)
        self._save()

    def _remove_ticker(self, ticker):
        g = self._current_group()
        if g and ticker in g["tickers"]:
            g["tickers"].remove(ticker)
        self.cfg["muted"] = [t for t in self.cfg["muted"] if t != ticker]
        self._save()
        self._refresh_groups()

    def _add_ticker(self):
        t = self.new_tick_ed.text().strip().upper()
        if not t:
            return
        target = self.add_cb.currentText()
        for g in self.cfg["groups"]:
            if t in g["tickers"]:
                g["tickers"].remove(t)
        for g in self.cfg["groups"]:
            if g["name"] == target:
                g["tickers"].append(t)
        self.new_tick_ed.clear()
        self._save()
        self._refresh_groups()

    def _add_all_from_moex(self):
        self.add_all_btn.setEnabled(False)
        self.add_all_btn.setText("Загрузка...")

        def work():
            secids = []
            err = None
            try:
                r = requests.get(ISS_TQBR_URL, timeout=15)
                r.raise_for_status()
                data = r.json()
                block = data.get("securities") or {}
                cols = block.get("columns") or []
                rows = block.get("data") or []
                idx = {c: i for i, c in enumerate(cols)}
                for row in rows:
                    i = idx.get("SECID")
                    if i is not None and i < len(row) and row[i]:
                        secids.append(str(row[i]).strip().upper())
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            QTimer.singleShot(0, lambda: self._apply_all(secids, err))

        threading.Thread(target=work, daemon=True).start()

    def _apply_all(self, secids, err):
        self.add_all_btn.setEnabled(True)
        self.add_all_btn.setText("＋ Все (MOEX)")
        if err or not secids:
            self.setWindowTitle(f"MOEX недоступен: {err or 'пустой список'}")
            return
        g = self._current_group()
        if g is None:
            self.cfg["groups"].append({"name": "Все TQBR", "tickers": []})
            g = self.cfg["groups"][-1]
        have = set()
        for gg in self.cfg["groups"]:
            have.update(gg["tickers"])
        added = 0
        for s in secids:
            if s not in have:
                g["tickers"].append(s)
                have.add(s)
                added += 1
        self._save()
        self._refresh_groups()
        self.setWindowTitle(f"Аукционы: настройки (добавлено {added} из {len(secids)})")

    def _move_selected(self):
        rows = sorted({i.row() for i in self.tick_table.selectedItems()})
        g = self._current_group()
        if g is None or not rows:
            return
        target_name = self.move_cb.currentText()
        if target_name == g["name"]:
            return
        moving = [g["tickers"][r] for r in rows if r < len(g["tickers"])]
        for t in moving:
            if t in g["tickers"]:
                g["tickers"].remove(t)
        for gg in self.cfg["groups"]:
            if gg["name"] == target_name:
                for t in moving:
                    if t not in gg["tickers"]:
                        gg["tickers"].append(t)
        self._save()
        self._refresh_groups()

    def _add_group(self):
        name = self.new_group_ed.text().strip()
        if not name:
            return
        if any(g["name"] == name for g in self.cfg["groups"]):
            return
        self.cfg["groups"].append({"name": name, "tickers": []})
        self.new_group_ed.clear()
        self._save()
        self._refresh_groups()

    def _del_group(self):
        g = self._current_group()
        if g is None:
            return
        self.cfg["groups"].remove(g)
        self._save()
        self._refresh_groups()