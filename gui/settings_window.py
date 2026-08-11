"""
Приблуда на python — окно "Настройки тикеров" (Tkinter Toplevel).

Отдельное окно поверх главного, открывается кнопкой "⚙ Настройки
тикеров". Редактирует ticker_settings.json (см. ticker_settings.py) —
единственный источник списка тикеров и ручных порогов для GUI и для
живого скринера / батч-запуска по истории.

Изменения сохраняются в файл СРАЗУ при каждом действии (добавление,
удаление, переключение "активен", сохранение в диалоге редактирования).
Но на уже запущенный live_screener.py они не влияют "на лету" — подписки
на WebSocket и настройки детекторов читаются один раз при старте
программы. Чтобы правки подействовали — нужно перезапустить программу.

Таблица не редактируется прямо в ячейках (в Tkinter это капризная
штука без сторонних библиотек) — вместо этого двойной клик по строке
открывает диалог редактирования (TickerEditDialog). Единственное
действие прямо из таблицы без диалога — кнопка "Вкл/Выкл выбранный":
быстро временно отключить бумагу (затемнить), не открывая лишних окон.

Поле поиска фильтрует таблицу по подстроке тикера "на лету" — когда
список большой, глазами искать неудобно. Если в поле "Новый тикер"
вписан тикер, который уже есть в списке, окно добавления не открывает
дубликат — вместо этого сразу выделяет существующую строку и открывает
её диалог редактирования (по сути "перейти к настройкам").

Окно можно закрепить поверх всех остальных окон галочкой "Поверх
всех окон" — удобно держать открытым и параллельно смотреть в главную
таблицу.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from ticker_settings import add_ticker, load_settings, remove_ticker, save_settings

COLUMNS = ("symbol", "active", "min_qty", "min_interval", "min_repeats")
HEADERS = {
    "symbol": "ТИКЕР",
    "active": "СТАТУС",
    "min_qty": "МИН.ЛОТОВ",
    "min_interval": "МИН.СЕК",
    "min_repeats": "МИН.ПОВТ.",
}
COLUMN_WIDTHS = {
    "symbol": 65,
    "active": 85,
    "min_qty": 80,
    "min_interval": 75,
    "min_repeats": 75,
}


def _fmt_active(active: bool) -> str:
    return "✓ активен" if active else "— отключён"


def _fmt_optional(value, suffix: str = "") -> str:
    return "авто" if value is None else f"{value}{suffix}"


class TickerEditDialog(tk.Toplevel):
    """Диалог редактирования порогов одного тикера — открывается
    двойным кликом по строке в SettingsWindow, либо автоматически при
    попытке добавить тикер, который уже есть в списке."""

    def __init__(self, parent: "SettingsWindow", symbol: str, override: dict):
        super().__init__(parent)
        self.parent = parent
        self.symbol = symbol
        self.title(f"Настройки {symbol}")
        self.resizable(False, False)
        self.grab_set()  # модальный — не даём тыкать в таблицу, пока диалог открыт

        self.active_var = tk.BooleanVar(value=override.get("active", True))
        self.min_qty_var = tk.StringVar(
            value="" if override.get("min_qty") is None else str(override["min_qty"])
        )
        self.min_interval_var = tk.StringVar(
            value="" if override.get("min_interval") is None else str(override["min_interval"])
        )
        self.min_repeats_var = tk.StringVar(
            value="" if override.get("min_repeats") is None else str(override["min_repeats"])
        )

        pad = {"padx": 10, "pady": 6}

        tk.Checkbutton(self, text="Активен (мониторить)", variable=self.active_var).grid(
            row=0, column=0, columnspan=2, sticky="w", **pad
        )

        tk.Label(self, text="Мин. лотов (пусто = авто по процентилю):").grid(
            row=1, column=0, sticky="w", **pad
        )
        tk.Entry(self, textvariable=self.min_qty_var, width=12).grid(row=1, column=1, **pad)

        tk.Label(self, text="Мин. сек интервала (пусто = из пресета):").grid(
            row=2, column=0, sticky="w", **pad
        )
        tk.Entry(self, textvariable=self.min_interval_var, width=12).grid(row=2, column=1, **pad)

        tk.Label(self, text="Мин. повторов (пусто = из пресета):").grid(
            row=3, column=0, sticky="w", **pad
        )
        tk.Entry(self, textvariable=self.min_repeats_var, width=12).grid(row=3, column=1, **pad)

        button_frame = tk.Frame(self)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(10, 12))
        tk.Button(button_frame, text="Сохранить", command=self._save).pack(side="left", padx=6)
        tk.Button(button_frame, text="Отмена", command=self.destroy).pack(side="left", padx=6)

    def _parse_optional(self, raw: str, kind, field_label: str):
        """Пустая строка -> None. Иначе -> число нужного типа. При
        ошибке разбора или неположительном числе показывает ошибку и
        возвращает строку "invalid" (сигнал вызывающему коду прервать
        сохранение, не закрывая диалог)."""
        raw = raw.strip()
        if not raw:
            return None
        try:
            value = kind(raw)
        except ValueError:
            messagebox.showerror("Ошибка", f"«{field_label}» должно быть числом или пустым.")
            return "invalid"
        if value <= 0:
            messagebox.showerror("Ошибка", f"«{field_label}» должно быть больше нуля.")
            return "invalid"
        return value

    def _save(self) -> None:
        min_qty = self._parse_optional(self.min_qty_var.get(), int, "Мин. лотов")
        if min_qty == "invalid":
            return
        min_interval = self._parse_optional(self.min_interval_var.get(), float, "Мин. сек")
        if min_interval == "invalid":
            return
        min_repeats = self._parse_optional(self.min_repeats_var.get(), int, "Мин. повторов")
        if min_repeats == "invalid":
            return

        self.parent.settings[self.symbol] = {
            "active": self.active_var.get(),
            "min_qty": min_qty,
            "min_interval": min_interval,
            "min_repeats": min_repeats,
        }
        save_settings(self.parent.settings)
        self.parent.refresh_table()
        self.destroy()


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Настройки тикеров")
        self.geometry("400x580")
        self.minsize(380, 320)

        self.settings = load_settings()

        info = tk.Label(
            self,
            text=(
                "Двойной клик по строке — изменить пороги.\n"
                "Применяются после перезапуска программы."
            ),
            justify="left",
            fg="#555555",
            font=("Segoe UI", 9),
        )
        info.pack(fill="x", padx=8, pady=(8, 4))

        top_frame = tk.Frame(self)
        top_frame.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(top_frame, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self.refresh_table())
        search_entry = tk.Entry(top_frame, textvariable=self.search_var, width=14)
        search_entry.pack(side="left", padx=(4, 10))
        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            top_frame, text="Поверх всех окон", variable=self.topmost_var,
            command=self._toggle_topmost,
        ).pack(side="left")

        self.tree = ttk.Treeview(self, columns=COLUMNS, show="headings", height=16)
        for col in COLUMNS:
            self.tree.heading(col, text=HEADERS[col])
            self.tree.column(col, width=COLUMN_WIDTHS[col], anchor="center")
        self.tree.tag_configure("inactive", foreground="#a0a0a0")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", self._on_double_click)

        add_frame = tk.Frame(self)
        add_frame.pack(fill="x", padx=8, pady=(4, 4))
        tk.Label(add_frame, text="Новый тикер:").pack(side="left")
        self.new_symbol_var = tk.StringVar()
        entry = tk.Entry(add_frame, textvariable=self.new_symbol_var, width=12)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _event: self._add_ticker())
        tk.Button(add_frame, text="Добавить", command=self._add_ticker).pack(side="left")

        action_frame = tk.Frame(self)
        action_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(
            action_frame, text="Вкл/Выкл выбранный", command=self._toggle_active
        ).pack(fill="x", pady=(0, 4))
        tk.Button(
            action_frame, text="Удалить выбранный", command=self._remove_ticker
        ).pack(fill="x")

        self.refresh_table()

    def _toggle_topmost(self) -> None:
        self.attributes("-topmost", self.topmost_var.get())

    def _visible_symbols(self) -> list[str]:
        query = self.search_var.get().strip().upper()
        symbols = sorted(self.settings)
        if not query:
            return symbols
        return [s for s in symbols if query in s]

    def refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for symbol in self._visible_symbols():
            override = self.settings[symbol]
            tag = "inactive" if not override.get("active", True) else ""
            self.tree.insert(
                "",
                "end",
                iid=symbol,
                values=(
                    symbol,
                    _fmt_active(override.get("active", True)),
                    _fmt_optional(override.get("min_qty")),
                    _fmt_optional(override.get("min_interval"), "с"),
                    _fmt_optional(override.get("min_repeats")),
                ),
                tags=(tag,) if tag else (),
            )

    def _selected_symbol(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _open_edit_dialog(self, symbol: str) -> None:
        TickerEditDialog(self, symbol, self.settings[symbol])

    def _on_double_click(self, _event) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        self._open_edit_dialog(symbol)

    def _add_ticker(self) -> None:
        symbol = self.new_symbol_var.get().strip().upper()
        if not symbol:
            return

        if symbol in self.settings:
            # Уже в списке — не плодим дубликат и не ругаемся ошибкой,
            # а сразу переходим к его настройкам, как будто кликнули по
            # строке в таблице.
            self.search_var.set("")
            self.new_symbol_var.set("")
            self.refresh_table()
            self.tree.selection_set(symbol)
            self.tree.see(symbol)
            self._open_edit_dialog(symbol)
            return

        add_ticker(self.settings, symbol)
        save_settings(self.settings)
        self.new_symbol_var.set("")
        self.refresh_table()

    def _remove_ticker(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        if not messagebox.askyesno("Удалить", f"Убрать {symbol} из мониторинга совсем?"):
            return
        remove_ticker(self.settings, symbol)
        save_settings(self.settings)
        self.refresh_table()

    def _toggle_active(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        override = self.settings[symbol]
        override["active"] = not override.get("active", True)
        save_settings(self.settings)
        self.refresh_table()