"""
Приблуда на python — отдельное окно «Поводыри рынка» со свечными
графиками и полосами Боллинджера. Запускается ОТДЕЛЬНО от скринера
(python leader_monitor.py), ничего в live_screener.py не меняет —
поэтому его можно включать/выключать независимо.
Для скальпера: ищем точку входа на графике поводыря — например, отскок
от НИЖНЕЙ полосы Боллинджера на покупку. Можно переключать таймфреймы
(1м/10м/1ч/1д) и поводырей (индекс, RGBI, юань, доллар, нефть).
Данные — из tg_bot/leader_data.py (MOEX ISS candles, без токена),
обновляются в фоне раз в LEADER_REFRESH_SEC секунд.
Нужен matplotlib: pip install matplotlib
Запуск (из корня проекта):
python leader_monitor.py
"""
import asyncio
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "tg_bot"))

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from leader_data import (
    LEADERS, TIMEFRAMES, BOLLINGER_PERIOD, BOLLINGER_STD,
    LeaderCache, leader_refresh_loop, bollinger,
)

REFRESH_MS = 10000          # перерисовка графика раз в 10 сек
DEFAULT_GEOMETRY = "840x580"


class LeaderMonitorWindow(tk.Tk):
    def __init__(self, cache: LeaderCache):
        super().__init__()
        self.cache = cache
        self.title("Поводыри рынка (Боллинджер)")
        self.geometry(DEFAULT_GEOMETRY)
        self.attributes("-topmost", True)

        top = tk.Frame(self)
        top.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(top, text="Поводырь:").pack(side="left", padx=(0, 4))
        self.leader_var = tk.StringVar(value=LEADERS[0][0])
        combo = ttk.Combobox(
            top, textvariable=self.leader_var,
            values=[l[0] for l in LEADERS], width=12, state="readonly",
        )
        combo.pack(side="left", padx=(0, 12))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._draw())

        tk.Label(top, text="Таймфрейм:").pack(side="left", padx=(0, 4))
        self.tf_var = tk.StringVar(value="10м")
        for label in TIMEFRAMES:
            ttk.Radiobutton(
                top, text=label, variable=self.tf_var, value=label,
                command=self._draw,
            ).pack(side="left", padx=2)

        self.topmost_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top, text="поверх окон", variable=self.topmost_var,
            command=self._toggle_topmost,
        ).pack(side="right")

        self.fig = Figure(figsize=(8, 5), dpi=95)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

        self._draw()
        self.after(REFRESH_MS, self._auto_refresh)

    def _toggle_topmost(self):
        self.attributes("-topmost", self.topmost_var.get())

    def _draw(self):
        secid = self.leader_var.get()
        tf = self.tf_var.get()
        candles = self.cache.get(secid, tf)
        name = next((l[4] for l in LEADERS if l[0] == secid), secid)
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        if not candles:
            ax.text(0.5, 0.5, "Нет данных (инструмент не отдаёт свечи)",
                    ha="center", va="center")
            ax.set_title(f"{name} — {tf}")
            ax.axis("off")
            self.canvas.draw()
            return

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        upper, mid, lower = bollinger(closes)
        n = len(candles)
        price_range = max(highs) - min(lows)
        min_body = max(price_range * 0.004, 1e-9)

        for i, c in enumerate(candles):
            is_up = c["close"] >= c["open"]
            col = "#1a7a1a" if is_up else "#a31515"
            ax.vlines(i, c["low"], c["high"], color=col, linewidth=0.8)
            body_lo = min(c["open"], c["close"])
            body_hi = max(c["open"], c["close"])
            body_h = max(body_hi - body_lo, min_body)
            ax.add_patch(Rectangle(
                (i - 0.35, body_lo), 0.7, body_h,
                facecolor=col, edgecolor=col, linewidth=0.4,
            ))

        xs = list(range(n))
        ax.plot(xs, upper, color="#3b78c2", linewidth=1.0, alpha=0.9, label="BB верх")
        ax.plot(xs, mid, color="#8a8a8a", linewidth=1.0, alpha=0.9, label="BB середина")
        ax.plot(xs, lower, color="#c23b3b", linewidth=1.0, alpha=0.9, label="BB низ")

        ax.set_xlim(-1, n)
        all_vals = [v for v in (highs + lows + upper + lower) if v is not None]
        ax.set_ylim(min(all_vals) - price_range * 0.05, max(all_vals) + price_range * 0.05)
        updated = self.cache.updated_at.strftime("%H:%M:%S") if self.cache.updated_at else "—"
        ax.set_title(
            f"{name} — {tf}   |   BB({BOLLINGER_PERIOD}, {BOLLINGER_STD}σ)   |   обновлено {updated}"
        )
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, loc="upper left")
        self.canvas.draw()

    def _auto_refresh(self):
        self._draw()
        self.after(REFRESH_MS, self._auto_refresh)


def start_cache_loop(cache: LeaderCache) -> None:
    try:
        asyncio.run(leader_refresh_loop(cache))
    except KeyboardInterrupt:
        pass


def main() -> None:
    cache = LeaderCache()
    threading.Thread(target=start_cache_loop, args=(cache,), daemon=True).start()
    app = LeaderMonitorWindow(cache)
    app.mainloop()


if __name__ == "__main__":
    main()