"""
Приблуда на python — лёгкий GUI (стандартная библиотека Tkinter, ничего
дополнительно ставить не нужно).

ВКЛАДКИ: окно теперь разбито на вкладки (ttk.Notebook):
  🤖 Роботы   — прежний вид целиком (устойчивые/лонг/шорт/группы),
                логика подсветки/звука/группировки не менялась.
  ⚖️ Арбитраж — снимки PairMonitor.snapshot() из shared_state.arb_rows:
                текущее отношение цен, обычный уровень (baseline),
                отклонение в % и статус (в норме / РАСХОЖДЕНИЕ).
  💰 Фандинг  — таблица ставок из shared_state.funding_rows.
  📰 Новости  — лента заголовков из shared_state.news_items.

Верхняя панель (статус, кнопки настроек/мини-окна/topmost/звука) и
калькулятор объёма остаются ВНЕ вкладок — видны всегда, независимо от
выбранной вкладки, так как это общие инструменты, не привязанные к
конкретному разделу.

Минималистичный дизайн (вкладка "Роботы"): вместо подписей
"ЛОНГ"/"ШОРТ" — зелёная и красная рамка вокруг соответствующей таблицы,
плюс текст строки того же цвета во ВСЕХ таблицах.

УСТОЙЧИВЫЕ РОБОТЫ (отдельная синяя рамка сверху, во всю ширину): робот,
который бьёт стабильно долго (repeats >= STABLE_REPEATS_THRESHOLD),
самый надёжный и самый интересный для торговли — но при этом самый
незаметный, потому что тонет среди постоянно меняющих порядок строк в
обычных таблицах (там сортировка по обратному отсчёту, который тикает
каждую секунду — отсюда ощущение "рябит в глазах"). Такие роботы
вытаскиваются в отдельную панель СВЕРХУ, ВСЕГДА НА ВИДУ, и сортируются
там по тикеру (не по обратному отсчёту) — порядок строк почти не
меняется, меняется только счётчик повторов, спокойно растущий.
STABLE_REPEATS_THRESHOLD подобран приблизительно — поменяй число, если
после наблюдения окажется мало/много.

Если по одному тикеру активно сразу несколько НЕустойчивых роботов (в
том числе разнонаправленных) — такие строки переезжают в отдельную
рамку СПРАВА от лонг/шорт. Устойчивые роботы в эту группировку не
попадают — они уже в своей отдельной панели сверху, вне зависимости от
того, сколько их таких на этом тикере.

ПОДСВЕТКА НОВЫХ/УМЕРШИХ СЕРИЙ: каждая серия однозначно определяется
тройкой (тикер, сторона, пресет) + start_ts (момент старта серии — не
меняется, пока серия жива, см. detectors/interval_robot.py). Новая
серия на один цикл обновления подсвечивается голубым фоном; пропавшая
рисуется ещё один последний раз серым текстом и на следующем цикле уже
не показывается — "мигнула и убралась". Работает во всех четырёх
таблицах одинаково, включая панель устойчивых.

ЗВУК: на каждую новую серию (если есть хотя бы одна) — короткий сигнал
(см. sound.py). Один сигнал за цикл обновления, даже если новых серий
несколько сразу. Отключается галочкой "🔊 Звук", состояние сохраняется
в ui_settings.json.

ГЕОМЕТРИЯ ОКНА: позиция и размер сохраняются в ui_settings.json при
закрытии окна и восстанавливаются при следующем запуске.

Двойной клик по ячейке ТИКЕРА в любой таблице копирует его текст в
буфер обмена — строка на секунду подсвечивается.

Обновляется раз в секунду из shared_state.rows, которые считает фоновый
поток (live_screener.py). Здесь нет прямого доступа к детекторам —
только чтение уже готового, отфильтрованного списка строк (см.
dashboard/collector.py). Вкладки арбитража/фандинга/новостей читаются
из shared_state.arb_rows / funding_rows / news_items тем же способом.

Кнопка "⚙ Настройки тикеров" открывает окно gui/settings_window.py.
Кнопка "🗗 Мини-окно" открывает/закрывает компактный оверлей
gui/mini_window.py — для закрепления поверх торгового терминала (там
пока панели устойчивых роботов нет — это compact-инструмент, добавим
туда позже, если понадобится).

Панель "Калькулятор объёма" — независимый инструмент, не связан с
детекторами. Считает лоты = К / шаг цены бумаги (см. calc_settings.py).
"""

import tkinter as tk
from collections import Counter
from tkinter import ttk

from calc_settings import load_rubles_per_point, save_rubles_per_point
from gui.mini_window import MiniWindow
from gui.settings_window import SettingsWindow
from gui.state import SharedState
from sound import play_new_series_sound
from ui_settings import load_ui_settings, save_ui_settings

COLUMNS = ("symbol", "qty", "next", "repeats", "interval", "preset")
HEADERS = {
    "symbol": "ТИК",
    "qty": "ОБ",
    "next": "СЛЕД",
    "repeats": "ПОВТ",
    "interval": "ИНТ",
    "preset": "ТИП",
}
COLUMN_WIDTHS = {
    "symbol": 60,
    "qty": 100,
    "next": 60,
    "repeats": 50,
    "interval": 60,
    "preset": 90,
}
SYMBOL_COLUMN_INDEX = COLUMNS.index("symbol")
REFRESH_MS = 1000
COPY_FLASH_MS = 350
DEFAULT_GEOMETRY = "980x900"
STABLE_REPEATS_THRESHOLD = 8  # от скольки повторов робот считается "устойчивым"

PRESET_LABELS = {
    "fast_strict": "быстр",
    "slow_strict": "медл",
    "fast_loose": "быстр.шир",
    "slow_loose": "медл.шир",
}

BUY_COLOR = "#1a7a1a"
SELL_COLOR = "#a31515"
GROUP_BORDER_COLOR = "#b8860b"
STABLE_BORDER_COLOR = "#3a6ea5"
COPY_FLASH_COLOR = "#fff2a8"
NEW_FLASH_COLOR = "#cfe8ff"
DYING_COLOR = "#999999"

ARB_COLUMNS = ("pair", "ratio", "baseline", "deviation", "status")
ARB_HEADERS = {
    "pair": "СВЯЗКА",
    "ratio": "ТЕКУЩЕЕ",
    "baseline": "ОБЫЧНОЕ",
    "deviation": "ОТКЛ. %",
    "status": "СТАТУС",
}
ARB_COLUMN_WIDTHS = {
    "pair": 160,
    "ratio": 100,
    "baseline": 100,
    "deviation": 90,
    "status": 140,
}

FUNDING_COLUMNS = ("name", "rate")
FUNDING_HEADERS = {"name": "ИНСТРУМЕНТ", "rate": "СТАВКА"}
FUNDING_COLUMN_WIDTHS = {"name": 160, "rate": 120}


def _sort_key(row: dict):
    seconds = row["seconds_to_next"]
    return seconds if seconds is not None else float("inf")


def _row_key(row: dict):
    """Стабильный идентификатор серии между обновлениями таблицы —
    см. докстринг модуля."""
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


class RobotDashboardWindow(tk.Tk):
    def __init__(self, shared_state: SharedState):
        super().__init__()
        self.shared_state = shared_state
        self.mini_window: MiniWindow | None = None

        ui_settings = load_ui_settings()

        self.title("Приблуда на python")
        self.geometry(ui_settings.get("main_window_geometry") or DEFAULT_GEOMETRY)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Идентификаторы серий, показанных на ПРОШЛОМ цикле обновления,
        # и их последние известные данные — для подсветки новых/умерших.
        self._prev_keys: set = set()
        self._prev_rows: dict = {}

        top_frame = tk.Frame(self)
        top_frame.pack(fill="x", padx=8, pady=(8, 4))

        self.status_label = tk.Label(top_frame, text="запуск...", anchor="w", font=("Segoe UI", 9))
        self.status_label.pack(side="left", fill="x", expand=True)

        self.mini_button = tk.Button(
            top_frame, text="🗗 Мини-окно", command=self._toggle_mini_window
        )
        self.mini_button.pack(side="right")

        tk.Button(
            top_frame, text="⚙ Настройки тикеров", command=self._open_settings
        ).pack(side="right", padx=(0, 6))

        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            top_frame, text="Поверх всех окон", variable=self.topmost_var,
            command=self._toggle_topmost,
        ).pack(side="right", padx=(0, 10))

        self.sound_var = tk.BooleanVar(value=ui_settings.get("sound_enabled", True))
        tk.Checkbutton(
            top_frame, text="🔊 Звук", variable=self.sound_var,
        ).pack(side="right", padx=(0, 10))

        self._make_calculator()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        robots_tab = tk.Frame(self.notebook)
        self.notebook.add(robots_tab, text="🤖 Роботы")
        self._make_robots_tab(robots_tab)

        arb_tab = tk.Frame(self.notebook)
        self.notebook.add(arb_tab, text="⚖️ Арбитраж")
        self._make_arb_tab(arb_tab)

        funding_tab = tk.Frame(self.notebook)
        self.notebook.add(funding_tab, text="💰 Фандинг")
        self._make_funding_tab(funding_tab)

        news_tab = tk.Frame(self.notebook)
        self.notebook.add(news_tab, text="📰 Новости")
        self._make_news_tab(news_tab)

        self.after(REFRESH_MS, self._refresh)

    def _make_robots_tab(self, parent: tk.Frame) -> None:
        # Устойчивые роботы — всегда на виду, во всю ширину, порядок
        # строк почти не скачет (сортировка по тикеру, не по обратному
        # отсчёту).
        stable_border = tk.Frame(parent, highlightbackground=STABLE_BORDER_COLOR, highlightthickness=2, bd=0)
        stable_border.pack(fill="x", padx=0, pady=(4, 4))
        self.stable_tree = self._make_tree(stable_border, height=6)

        # Слева — лонг сверху, шорт снизу. Справа, во всю высоту обоих —
        # тикеры с несколькими одновременно активными НЕустойчивыми
        # роботами.
        container = tk.Frame(parent)
        container.pack(fill="both", expand=True, padx=0, pady=(4, 4))
        container.grid_columnconfigure(0, weight=2)
        container.grid_columnconfigure(1, weight=1)
        container.grid_rowconfigure(0, weight=1)

        left = tk.Frame(container)
        left.grid(row=0, column=0, sticky="nsew")

        right = tk.Frame(container)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        buy_border = tk.Frame(left, highlightbackground=BUY_COLOR, highlightthickness=2, bd=0)
        buy_border.pack(fill="both", expand=True, pady=(0, 4))
        self.buy_tree = self._make_tree(buy_border)

        sell_border = tk.Frame(left, highlightbackground=SELL_COLOR, highlightthickness=2, bd=0)
        sell_border.pack(fill="both", expand=True)
        self.sell_tree = self._make_tree(sell_border)

        group_border = tk.Frame(right, highlightbackground=GROUP_BORDER_COLOR, highlightthickness=2, bd=0)
        group_border.pack(fill="both", expand=True)
        self.group_tree = self._make_tree(group_border, height=24)

    def _make_arb_tab(self, parent: tk.Frame) -> None:
        self.arb_tree = ttk.Treeview(parent, columns=ARB_COLUMNS, show="headings", height=10)
        for col in ARB_COLUMNS:
            self.arb_tree.heading(col, text=ARB_HEADERS[col])
            self.arb_tree.column(col, width=ARB_COLUMN_WIDTHS[col], anchor="center")
        self.arb_tree.tag_configure("triggered", foreground=SELL_COLOR)
        self.arb_tree.pack(fill="both", expand=True, padx=4, pady=4)

    def _make_funding_tab(self, parent: tk.Frame) -> None:
        self.funding_updated_label = tk.Label(parent, text="", anchor="w", font=("Segoe UI", 8))
        self.funding_updated_label.pack(fill="x", padx=4, pady=(4, 0))

        self.funding_tree = ttk.Treeview(parent, columns=FUNDING_COLUMNS, show="headings", height=12)
        for col in FUNDING_COLUMNS:
            self.funding_tree.heading(col, text=FUNDING_HEADERS[col])
            self.funding_tree.column(col, width=FUNDING_COLUMN_WIDTHS[col], anchor="center")
        self.funding_tree.pack(fill="both", expand=True, padx=4, pady=4)

    def _make_news_tab(self, parent: tk.Frame) -> None:
        self.news_updated_label = tk.Label(parent, text="", anchor="w", font=("Segoe UI", 8))
        self.news_updated_label.pack(fill="x", padx=4, pady=(4, 0))

        text_frame = tk.Frame(parent)
        text_frame.pack(fill="both", expand=True, padx=4, pady=4)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        self.news_text = tk.Text(
            text_frame, wrap="word", state="disabled",
            yscrollcommand=scrollbar.set, font=("Segoe UI", 9),
        )
        self.news_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.news_text.yview)

    def _make_calculator(self) -> None:
        calc_frame = tk.LabelFrame(self, text="Калькулятор объёма (лоты = К / шаг цены)")
        calc_frame.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(calc_frame, text="Шаг цены:").grid(row=0, column=0, padx=(8, 4), pady=6, sticky="e")
        self.calc_step_var = tk.StringVar()
        step_entry = tk.Entry(calc_frame, textvariable=self.calc_step_var, width=10)
        step_entry.grid(row=0, column=1, pady=6)
        step_entry.bind("<KeyRelease>", lambda _e: self._recalc_lots())

        tk.Label(calc_frame, text="К, руб/пункт:").grid(row=0, column=2, padx=(16, 4), pady=6, sticky="e")
        self.calc_k_var = tk.StringVar(value=str(load_rubles_per_point()))
        k_entry = tk.Entry(calc_frame, textvariable=self.calc_k_var, width=8)
        k_entry.grid(row=0, column=3, pady=6)
        k_entry.bind("<KeyRelease>", lambda _e: self._recalc_lots())
        k_entry.bind("<FocusOut>", lambda _e: self._save_k())
        k_entry.bind("<Return>", lambda _e: self._save_k())

        self.calc_result_label = tk.Label(calc_frame, text="Лотов: —", font=("Segoe UI", 9, "bold"))
        self.calc_result_label.grid(row=0, column=4, padx=(16, 8), pady=6, sticky="w")

    def _recalc_lots(self) -> None:
        try:
            step = float(self.calc_step_var.get().replace(",", "."))
            k = float(self.calc_k_var.get().replace(",", "."))
            if step <= 0 or k <= 0:
                raise ValueError
        except ValueError:
            self.calc_result_label.config(text="Лотов: —")
            return

        lots = max(1, round(k / step))
        actual = lots * step
        self.calc_result_label.config(text=f"Лотов: {lots}  (≈{actual:.2f} ₽/пункт)")

    def _save_k(self) -> None:
        try:
            k = float(self.calc_k_var.get().replace(",", "."))
            if k <= 0:
                return
        except ValueError:
            return
        save_rubles_per_point(k)

    def _open_settings(self) -> None:
        SettingsWindow(self)

    def _toggle_mini_window(self) -> None:
        if self.mini_window is not None:
            self.mini_window.destroy()
            self.mini_window = None
            self.mini_button.config(text="🗗 Мини-окно")
            return
        self.mini_window = MiniWindow(self, self.shared_state, on_close=self._on_mini_closed)
        self.mini_button.config(text="🗗 Закрыть мини-окно")

    def _on_mini_closed(self) -> None:
        self.mini_window = None
        self.mini_button.config(text="🗗 Мини-окно")

    def _toggle_topmost(self) -> None:
        self.attributes("-topmost", self.topmost_var.get())

    def _on_close(self) -> None:
        """Сохраняем звук и геометрию перед закрытием — читаем текущий
        файл настроек, чтобы не затереть то, что уже сохранило
        мини-окно (если оно закрывалось раньше)."""
        settings = load_ui_settings()
        settings["sound_enabled"] = self.sound_var.get()
        settings["main_window_geometry"] = self.geometry()
        save_ui_settings(settings)
        self.destroy()

    def _make_tree(self, parent: tk.Frame, height: int = 12) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=COLUMNS, show="headings", height=height)
        for col in COLUMNS:
            tree.heading(col, text=HEADERS[col])
            tree.column(col, width=COLUMN_WIDTHS[col], anchor="center")
        tree.tag_configure("buy", foreground=BUY_COLOR)
        tree.tag_configure("sell", foreground=SELL_COLOR)
        tree.tag_configure("new_flash", background=NEW_FLASH_COLOR)
        tree.tag_configure("dying", foreground=DYING_COLOR)
        tree.bind("<Double-1>", lambda event, t=tree: _handle_symbol_copy(t, event))
        tree.pack(fill="both", expand=True, padx=2, pady=2)
        return tree

    @staticmethod
    def _fill_tree(tree: ttk.Treeview, rows: list[dict], new_keys: set, dying_keys: set) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            qty_str = "-".join(str(q) for q in row["qty_variants"])
            interval = row["interval"]
            interval_str = f"{interval:.1f}с" if interval is not None else "-"
            seconds = row["seconds_to_next"]
            next_str = f"{seconds:.0f}с" if seconds is not None else "-"
            preset_str = PRESET_LABELS.get(row["preset"], row["preset"])

            key = _row_key(row)
            if key in dying_keys:
                tags = ("dying",)
            elif key in new_keys:
                tags = (row["side"], "new_flash")
            else:
                tags = (row["side"],)

            tree.insert(
                "",
                "end",
                values=(
                    row["symbol"],
                    qty_str,
                    next_str,
                    row["repeats"],
                    interval_str,
                    preset_str,
                ),
                tags=tags,
            )

    def _refresh_arb_tab(self) -> None:
        self.arb_tree.delete(*self.arb_tree.get_children())
        for row in self.shared_state.arb_rows:
            ratio = row["current_ratio"]
            baseline = row["baseline"]
            deviation = row["deviation_pct"]

            ratio_str = f"{ratio:.4f}" if ratio is not None else "-"
            baseline_str = f"{baseline:.4f}" if baseline is not None else "-"
            deviation_str = f"{deviation:+.2f}%" if deviation is not None else "-"
            status_str = "⚡ РАСХОЖДЕНИЕ" if row["triggered"] else "в норме"

            tags = ("triggered",) if row["triggered"] else ()

            self.arb_tree.insert(
                "", "end",
                values=(
                    f"{row['pair_name']} ({row['symbol_a']}/{row['symbol_b']})",
                    ratio_str,
                    baseline_str,
                    deviation_str,
                    status_str,
                ),
                tags=tags,
            )

    def _refresh_funding_tab(self) -> None:
        updated = self.shared_state.funding_updated_at
        self.funding_updated_label.config(
            text=f"Обновлено: {updated}" if updated else "Ещё не загружено..."
        )

        self.funding_tree.delete(*self.funding_tree.get_children())
        for row in self.shared_state.funding_rows:
            self.funding_tree.insert("", "end", values=(row["name"], row["rate_str"]))

    def _refresh_news_tab(self) -> None:
        updated = self.shared_state.news_updated_at
        self.news_updated_label.config(
            text=f"Обновлено: {updated}" if updated else "Ещё не загружено..."
        )

        self.news_text.config(state="normal")
        self.news_text.delete("1.0", "end")
        for item in self.shared_state.news_items:
            line = f"• {item['title']}"
            if item["time"]:
                line += f"  ({item['time']})"
            self.news_text.insert("end", line + "\n")
            if item["url"]:
                self.news_text.insert("end", f"  {item['url']}\n")
            self.news_text.insert("end", "\n")
        self.news_text.config(state="disabled")

    def _refresh(self) -> None:
        rows = list(self.shared_state.rows)  # копия — без гонки с фоновым потоком
        current_by_key = {_row_key(r): r for r in rows}
        current_keys = set(current_by_key)

        new_keys = current_keys - self._prev_keys
        dying_keys = self._prev_keys - current_keys
        dying_rows = [self._prev_rows[k] for k in dying_keys if k in self._prev_rows]

        if new_keys and self.sound_var.get():
            play_new_series_sound(self)

        # Умирающие строки показываем ещё один (последний) раз вместе с
        # живыми, чтобы не пропасть из таблицы мгновенно.
        display_rows = rows + dying_rows

        # Устойчивые роботы (много повторов) вытаскиваются в свою
        # панель сверху вне зависимости от группировки — их не должно
        # мотать между обычными таблицами.
        stable_rows = [r for r in display_rows if r["repeats"] >= STABLE_REPEATS_THRESHOLD]
        stable_rows.sort(key=lambda r: r["symbol"])
        remaining_rows = [r for r in display_rows if r["repeats"] < STABLE_REPEATS_THRESHOLD]

        # Тикеры с несколькими одновременно активными НЕустойчивыми
        # роботами переезжают в правую рамку. Группировка считается
        # только по ЖИВЫМ (не умирающим) НЕустойчивым строкам.
        counts = Counter(r["symbol"] for r in rows if r["repeats"] < STABLE_REPEATS_THRESHOLD)
        grouped_symbols = {symbol for symbol, count in counts.items() if count > 1}

        single_rows = [r for r in remaining_rows if r["symbol"] not in grouped_symbols]
        grouped_rows = [r for r in remaining_rows if r["symbol"] in grouped_symbols]
        grouped_rows.sort(key=lambda r: (r["symbol"], _sort_key(r)))

        buy_rows = sorted((r for r in single_rows if r["side"] == "buy"), key=_sort_key)
        sell_rows = sorted((r for r in single_rows if r["side"] == "sell"), key=_sort_key)

        alive_grouped_count = len(
            [r for r in rows if r["repeats"] < STABLE_REPEATS_THRESHOLD and r["symbol"] in grouped_symbols]
        )
        alive_stable_count = len([r for r in rows if r["repeats"] >= STABLE_REPEATS_THRESHOLD])

        self._fill_tree(self.stable_tree, stable_rows, new_keys, dying_keys)
        self._fill_tree(self.buy_tree, buy_rows, new_keys, dying_keys)
        self._fill_tree(self.sell_tree, sell_rows, new_keys, dying_keys)
        self._fill_tree(self.group_tree, grouped_rows, new_keys, dying_keys)

        self._refresh_arb_tab()
        self._refresh_funding_tab()
        self._refresh_news_tab()

        self.status_label.config(
            text=(
                f"{self.shared_state.status}  |  "
                f"{len(buy_rows)} лонг / {len(sell_rows)} шорт / "
                f"{alive_grouped_count} в группах / {alive_stable_count} устойчивых"
            )
        )

        self._prev_keys = current_keys
        self._prev_rows = current_by_key

        self.after(REFRESH_MS, self._refresh)