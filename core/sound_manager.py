"""
Приблуда на python — управление звуковыми уведомлениями.
"""
import json
from pathlib import Path
import winsound

BASE_DIR = Path(__file__).resolve().parent.parent
SOUND_CONFIG_FILE = BASE_DIR / "sound_config.json"


class SoundManager:
    """Управление звуками"""
    
    def __init__(self):
        self.enabled = True
        self._load_config()
    
    def _load_config(self):
        """Загрузка настроек звуков"""
        if SOUND_CONFIG_FILE.exists():
            try:
                with open(SOUND_CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.enabled = config.get("enabled", True)
            except:
                self.enabled = True
    
    def _save_config(self):
        """Сохранение настроек звуков"""
        config = {"enabled": self.enabled}
        with open(SOUND_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    
    def play_limit_alert(self):
        """Звук при касании планки"""
        if not self.enabled:
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except:
            pass
    
    def play_trade_alert(self):
        """Звук при обнаружении робота"""
        if not self.enabled:
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except:
            pass
    
    def toggle(self):
        """Переключить включение/выключение звуков"""
        self.enabled = not self.enabled
        self._save_config()
        return self.enabled