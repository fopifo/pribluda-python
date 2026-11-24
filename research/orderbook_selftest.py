"""
orderbook_selftest.py — генератор синтетического стакана для проверки детектора.
Слой-«мигалка»: SBER bids 299.30, 5000 лотов, присутствует 5с каждые 20с.
Плюс непериодический шум (не должен попасть в кандидаты).
Пишет data/orderbook_selftest.jsonl.
"""
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "orderbook_selftest.jsonl"


def main():
    random.seed(42)
    t0 = 1794750000000
    rows = []
    for i in range(1200):  # 600с при 2Гц
        ts = t0 + i * 500
        sec = i * 0.5
        layer_on = (sec % 20) < 5
        bids = [
            [299.30, 5000 if layer_on else 0],
            [299.29, 100],
            [299.28, 100],
        ]
        asks = [
            [299.31, 100],
            [299.32, 100],
            [299.33, 3000 if random.random() < 0.05 else 0],  # непериодический шум
        ]
        rows.append({
            "symbol": "SBER", "timestamp": ts,
            "best_bid": 299.30, "best_ask": 299.31,
            "bids": bids, "asks": asks,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"[OK] записано {len(rows)} снапшотов -> {OUT.name}")
    print("Ожидание: детектор должен найти SBER bids 299.30 период~20с")


if __name__ == "__main__":
    main()