"""
Приблуда на python — единая точка входа для работы с Quik.
Запускает GUI (Tkinter) + фоновый поток приёма UDP из Quik.
Не использует WebSocket Алора.
"""

import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from gui.state import SharedState
from gui.window import RobotDashboardWindow
from quik.quik_backend import start_backend


def main():
    shared_state = SharedState()

    backend_thread = threading.Thread(
        target=start_backend,
        args=(shared_state,),
        daemon=True,
        name="quik-backend",
    )
    backend_thread.start()

    app = RobotDashboardWindow(shared_state)
    app.mainloop()


if __name__ == "__main__":
    main()