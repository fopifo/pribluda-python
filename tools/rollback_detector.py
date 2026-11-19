"""Откат detectors/interval_robot.py к v13.4 (убирает правки v14.0)."""
import re

path = "detectors/interval_robot.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Убираем suppress_alias (линейный поиск убивает скорость)
content = re.sub(
    r'self\.suppress_alias = settings\.get\("suppress_alias", True\)',
    'self.suppress_alias = settings.get("suppress_alias", False)',
    content
)

# 2. Убираем wide_qty_when_regular (regularity на каждый trade = тормоза)
content = re.sub(
    r'self\.wide_qty_when_regular = settings\.get\("wide_qty_when_regular", True\)',
    'self.wide_qty_when_regular = settings.get("wide_qty_when_regular", False)',
    content
)

# 3. Возвращаем grid_lock_min_count к min_repeats
content = re.sub(
    r'self\.grid_lock_min_count = settings\.get\("grid_lock_min_count", 3\)',
    'self.grid_lock_min_count = settings.get("grid_lock_min_count", self.min_repeats)',
    content
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("✅ Откат к v13.4 завершён")