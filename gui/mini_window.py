"""
Приблуда на python — компактное окно-оверлей для мониторинга поверх
торгового терминала.

Показывает те же активные серии, что и главное окно, но одной узкой
таблицей — не делит buy/sell на отдельные блоки, вместо этого красит
строку целиком в зелёный (лонг) или красный (шорт) прямо по тексту,
раз отдельного столбца под сторону тут нет. Шрифт мельче, строки
теснее — чтобы окно занимало на экране минимум места.

Столбцы слева направо: СЛЕД, ТИК, ЛОТ, ИНТ, ПОВТ.

ПОДСВЕТКА НОВЫХ/УМЕРШИХ СЕРИЙ — та же логика, что и в главном окне
(gui/window.py, см. докстринг там): идентификатор серии — (тикер,
сторона, пресет, start_ts). Новая серия на один цикл подсвечивается
голубым фоном, пропавшая рисуется ещё один последний раз серым текстом
и на следующем цикле уже не показывается.

ЗВУК: на каждую новую серию — короткий сигнал (см. sound.py), один раз
за цикл, даже если новых серий несколько сразу. Отключается своей
галочкой "🔊" (независимо от главного окна — у каждого окна своя
галочка, но оба читают/пишут одно и то же ui_settings.json, поэтому
при следующем запуске оба открываются с тем состоянием, какое было
сохранено последним).

ГЕОМЕТРИЯ: позиция и размер сохраняются в ui_settings.json при закрытии
окна и восстанавливаются при следующем открытии — не нужно каждый раз
подгонять окно заново под угол экрана поверх терминала.

Двойной клик по ячейке ТИКЕРА копирует его в буфер обмена (строка
коротко подсвечивается) — так же, как в главном окне.

Открывается сразу закреплённым поверх остальных окон (в этом весь
смысл — мониторить поверх терминала), галочку "поверх окон" можно
снять, если понадобится.
"""

import tkinter as tk
from tkinter import ttk

from gui.state import SharedState
from sound import play_new_series_sound
from ui_settings import load_ui_settings, save_ui_settings

COLUMNS = ("next", "symbol", "qty", "interval", "repeats")
HEADERS = {
    "next": "СЛЕД",
    "symbol": "ТИК",
    "qty": "ЛОТ",
    "interval": "ИНТ",
    "repeats": "ПОВТ",
}
COLUMN_WIDTHS = {
    "next": 52,
    "symbol": 55,
    "qty": 65,
    "interval": 48,
    "repeats": 42,
}
SYMBOL_COLUMN_INDEX = COLUMNS.index("symbol")
REFRESH_MS = 1000
COPY_FLASH_MS = 350
STYLE_NAME = "Mini.Treeview"
DEFAULT_GEOMETRY = "320x420"
COPY_FLASH_COLOR = "#fff2a8"
NEW_FLASH_COLOR = "#cfe8ff"
DYING_COLOR = "#999999"


def _sort_key(row: dict):
    seconds = row["seconds_to_next"]
    return seconds if seconds is not None else float("inf")


def _row_key(row: dict):
    """Стабильный идентификатор серии между обновлениями — см.
    докстринг модуля и gui/window.py."""
    return (row["symbol"], row["side"], row["preset"], row["start_ts"])


def _handle_symbol_copy(tree: ttk.Treeview, event) -> None:
    """Двойной клик по ячейке столбца ТИКЕР — копирует значение в буфер
    обмена и коротко подсвечивает строку. Клик по любой другой ячейке
    ничего не делает."""
    if tree.identify("region", event.x, event.y) != "cell":
        return
    column = tree.identify_column(event.x)
    row_id = tree.identify_row(event.y)
    if not row_id:
        return

    col_index = int(column.replace("#", "")) - 1
    if col_index != SYMBOL_COLUMN_INDEX:
        return

    symbol = tree.item(row_id, "values")[SYMBOL_COLUMN_INDEX]
    tree.clipboard_clear()
    tree.clipboard_append(str(symbol))

    original_tags = tree.item(row_id, "tags")
    tree.tag_configure("copied_flash", background=COPY_FLASH_COLOR)
    tree.item(row_id, tags=tuple(original_tags) + ("copied_flash",))
    tree.after(COPY_FLASH_MS, lambda: tree.item(row_id, tags=original_tags))


class MiniWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, shared_state: SharedState, on_close=None):
        super().__init__(parent)
        self.shared_state = shared_state
        self.on_close = on_close

        ui_settings = load_ui_settings()

        self.title("Роботы")
        self.geometry(ui_settings.get("mini_window_geometry") or DEFAULT_GEOMETRY)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._prev_keys: set = set()
        self._prev_rows: dict = {}

        top = tk.Frame(self)
        top.pack(fill="x", padx=4, pady=(4, 2))
        self.topmost_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top, text="поверх окон", variable=self.topmost_var,
            command=self._toggle_topmost, font=("Segoe UI", 8),
        ).pack(side="left")
        self.sound_var = tk.BooleanVar(value=ui_settings.get("sound_enabled", True))
        tk.Checkbutton(
            top, text="🔊", variable=self.sound_var, font=("Segoe UI", 8),
        ).pack(side="left", padx=(8, 0))

        style = ttk.Style(self)
        style.configure(STYLE_NAME, font=("Segoe UI", 8), rowheight=16)
        style.configure(f"{STYLE_NAME}.Heading", font=("Segoe UI", 8, "bold"))

        self.tree = ttk.Treeview(self, columns=COLUMNS, show="headings", style=STYLE_NAME)
        for col in COLUMNS:
            self.tree.heading(col, text=HEADERS[col])
            self.tree.column(col, width=COLUMN_WIDTHS[col], anchor="center")
        self.tree.tag_configure("buy", foreground="#1a7a1a")
        self.tree.tag_configure("sell", foreground="#a31515")
        self.tree.tag_configure("new_flash", background=NEW_FLASH_COLOR)
        self.tree.tag_configure("dying", foreground=DYING_COLOR)
        self.tree.bind("<Double-1>", lambda event: _handle_symbol_copy(self.tree, event))
        self.tree.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._refresh()

    def _toggle_topmost(self) -> None:
        self.attributes("-topmost", self.topmost_var.get())

    def _handle_close(self) -> None:
        """Сохраняем звук и геометрию перед закрытием — читаем текущий
        файл настроек, чтобы не затереть то, что уже сохранило главное
        окно (если оно ещё открыто и закроется позже)."""
        settings = load_ui_settings()
        settings["sound_enabled"] = self.sound_var.get()
        settings["mini_window_geometry"] = self.geometry()
        save_ui_settings(settings)

        if self.on_close:
            self.on_close()
        self.destroy()

    def _refresh(self) -> None:
        rows = list(self.shared_state.rows)
        current_by_key = {_row_key(r): r for r in rows}
        current_keys = set(current_by_key)

        new_keys = current_keys - self._prev_keys
        dying_keys = self._prev_keys - current_keys
        dying_rows = [self._prev_rows[k] for k in dying_keys if k in self._prev_rows]

        if new_keys and self.sound_var.get():
            play_new_series_sound(self)

        display_rows = sorted(rows + dying_rows, key=_sort_key)

        self.tree.delete(*self.tree.get_children())
        for row in display_rows:
            interval = row["interval"]
            interval_str = f"{interval:.0f}с" if interval is not None else "-"
            seconds = row["seconds_to_next"]
            next_str = f"{seconds:.0f}с" if seconds is not None else "-"
            qty_str = "-".join(str(q) for q in row["qty_variants"])

            key = _row_key(row)
            if key in dying_keys:
                tags = ("dying",)
            elif key in new_keys:
                tags = (row["side"], "new_flash")
            else:
                tags = (row["side"],)

            self.tree.insert(
                "",
                "end",
                values=(next_str, row["symbol"], qty_str, interval_str, row["repeats"]),
                tags=tags,
            )

        self._prev_keys = current_keys
        self._prev_rows = current_by_key

        self.after(REFRESH_MS, self._refresh)