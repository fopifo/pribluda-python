"""
Приблуда на python — единая точка входа.
"""
import sys
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Перехват stdout/stderr для pythonw
if sys.stdout is None or sys.stderr is None:
    log_path = BASE_DIR / "output" / "main.log"
    log_path.parent.mkdir(exist_ok=True)
    _f = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None: sys.stdout = _f
    if sys.stderr is None: sys.stderr = _f

from PySide6.QtWidgets import QApplication
from gui.state import SharedState
from gui.main_window import MainWindow, QSS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["quik", "alor", "tinvest"], default="quik")
    args = parser.parse_args()
    
    shared = SharedState()
    
    if args.source == "quik":
        from connectors.quik.backend import start_backend
        start_backend(shared)
    
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    
    main_window = MainWindow(shared)
    main_window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()