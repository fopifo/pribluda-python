"""
Приблуда на python — единая точка входа: поднимает разом основной
скринер (роботы + арбитраж + GUI + push-уведомления в Telegram) и
интерактивное Telegram-меню, одной командой, одним процессом.

Раньше это были два раздельных терминала (live_screener.py и
tg_bot/menu_bot.py) — неудобно, и легко случайно запустить второй
экземпляр meню-бота поверх уже работающего (тогда оба процесса
опрашивают Telegram одновременно и оба отвечают на одни и те же
нажатия — если увидишь задвоенные ответы бота, в первую очередь
проверь, не открыт ли где-то ещё старый терминал с menu_bot.py).

С этого момента menu_bot.py напрямую лучше не запускать отдельно —
только через этот файл, чтобы не было двух живых экземпляров.

БЕЗ КОНСОЛИ (запуск через pythonw.exe / start_gui.bat): у pythonw.exe
нет консоли вообще, поэтому sys.stdout/stderr равны None — любой
print() в проекте (а их много: статусы, ошибки подключения) уронит
программу ДО открытия окна. Поэтому в самом начале файла, раньше любых
других импортов, проверяем это и в таком случае перенаправляем весь
вывод в logs/console.log — так же удобно смотреть, что происходило,
если что-то пошло не так молча (окно просто не появилось).

Архитектура (кто в каком потоке):
  - ГЛАВНЫЙ поток — Tkinter (окно GUI live_screener.py). Жёсткое
    требование самого Tkinter, обойти нельзя.
  - Поток "live-screener-backend" — WebSocket, детекторы роботов,
    арбитражные мониторы, watchdog, push-уведомления.
  - Поток "telegram-menu-bot" — независимый asyncio-цикл меню-бота.

Остановка — закрой окно GUI: оба фоновых потока помечены как daemon,
завершатся автоматически вместе с процессом.
"""

import sys
from pathlib import Path

# ВАЖНО: этот блок должен идти раньше любых других импортов и раньше
# первого print() где бы то ни было в проекте — иначе безконсольный
# запуск (pythonw.exe) упадёт с AttributeError на первом же print().
if sys.stdout is None or sys.stderr is None:
    _log_dir = Path(__file__).resolve().parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_file = open(_log_dir / "console.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
    print(f"\n=== Запуск без консоли, {Path(__file__).name} ===")

import threading

BASE_DIR = Path(__file__).resolve().parent
TG_BOT_DIR = BASE_DIR / "tg_bot"

# menu_bot.py и его соседи (funding.py, news_moex.py) импортируют друг
# друга как простые модули верхнего уровня (from funding import ...),
# а не как пакет tg_bot.* — так и было задумано, чтобы menu_bot.py
# можно было запускать напрямую. Чтобы то же самое работало и при
# импорте отсюда, явно добавляем tg_bot/ в sys.path ДО импорта.
sys.path.insert(0, str(TG_BOT_DIR))

from gui.state import SharedState
from gui.window import RobotDashboardWindow
from live_screener import start_backend
from menu_bot import main as menu_bot_main

import asyncio


def start_menu_bot() -> None:
    try:
        asyncio.run(menu_bot_main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Меню-бот аварийно остановился: {e}")


def main() -> None:
    shared_state = SharedState()

    backend_thread = threading.Thread(
        target=start_backend,
        args=(shared_state,),
        daemon=True,
        name="live-screener-backend",
    )
    backend_thread.start()

    menu_bot_thread = threading.Thread(
        target=start_menu_bot,
        daemon=True,
        name="telegram-menu-bot",
    )
    menu_bot_thread.start()

    app = RobotDashboardWindow(shared_state)
    app.mainloop()


if __name__ == "__main__":
    main()