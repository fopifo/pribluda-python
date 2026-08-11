"""
Приблуда на python — общее состояние между фоновым потоком (WebSocket +
детекторы, live_screener.py) и GUI (Tkinter, работает в главном потоке).

Фоновый поток раз в секунду (rows) или раз в несколько секунд (арбитраж,
фандинг, новости — они не такие срочные) перезаписывает соответствующий
атрибут целиком новым списком/значением — GUI по своему таймеру читает
текущее значение. Замена атрибута на новый объект — атомарная операция
в Python (из-за GIL), отдельная блокировка для такого простого случая
не нужна.
"""


class SharedState:
    def __init__(self):
        self.rows: list[dict] = []
        self.status: str = "запуск..."
        self.arb_rows: list[dict] = []       # снимки PairMonitor.snapshot()
        self.funding_rows: list[dict] = []   # {"name":..., "rate_str":...}
        self.funding_updated_at: str = ""
        self.news_items: list[dict] = []     # {"title":..., "time":..., "url":...}
        self.news_updated_at: str = ""