"""
Приблуда на python — короткий звуковой сигнал на новую серию.

Windows: winsound (стандартная библиотека, ничего ставить не надо).
Если winsound недоступен (не Windows) — используем системный "bell"
Tkinter (widget.bell()) — звучит проще и тише, но работает везде без
дополнительных библиотек и без доп. установки.
"""

try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False


def play_new_series_sound(widget) -> None:
    if _HAS_WINSOUND:
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        except Exception:
            pass
    widget.bell()