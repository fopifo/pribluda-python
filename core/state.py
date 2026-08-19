"""
Приблуда на python — общее состояние (SharedState) между бэкендом и GUI.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class SharedState:
    rows: List[Dict[str, Any]] = field(default_factory=list)
    batch_flash: Dict[str, float] = field(default_factory=dict)
    # Здесь можно добавить другие общие поля по мере необходимости