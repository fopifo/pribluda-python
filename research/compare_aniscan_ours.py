"""
Сравнение с Aniscan v3 (2026-11-09): матчинг по МОДЕЛИ СУЩНОСТЕЙ.
Aniscan: робот = сущность (robot.id) с жизненным циклом INSERT/DELETE,
operationCount = удары за ВСЮ жизнь, period = таймер робота.
Мы: снимок серии (окно [start, end], repeats = удары в окне).
v3 НЕ сравнивает repeats с operationCount (разный смысл величин).
Критерии совпадения:
  1) ticker + side;
  2) |наш interval_avg - их period| <= max(2с, 20%);
  3) наше окно серии пересекает их жизнь робота [INSERT, DELETE] +-60с.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
GRACE_SEC = 60.0


def load_jsonl(filepath):
    records = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def our_start_ts(rec):
    if 'start_ts' in rec:
        return float(rec['start_ts'])
    if 'start_ms' in rec:
        return float(rec['start_ms']) / 1000.0
    return None


def our_end_ts(rec):
    for key in ('end_ts', 'last_ts'):
        if key in rec:
            return float(rec[key])
    st = our_start_ts(rec)
    if st is None:
        return None
    return st + float(rec.get('repeats', 1)) * float(rec.get('interval_avg', 0.0))


def build_aniscan_robots(events):
    """Группирует события INSERT/DELETE по robot.id -> сущность робота."""
    by_id = defaultdict(list)
    for e in events:
        rid = (e.get('robot') or {}).get('id')
        if rid:
            by_id[rid].append(e)
    robots = []
    for rid, evs in by_id.items():
        times = []
        for e in evs:
            try:
                dt = datetime.fromisoformat(e['createDttm'])
                times.append(dt.timestamp())
            except (KeyError, ValueError):
                continue
        if not times:
            continue
        first = evs[0]
        robots.append({
            'id': rid,
            'ticker': first.get('ticker'),
            'side': (first.get('operationType') or '').upper(),
            'period': float(first.get('period') or 0),
            'op_count': max(int(e.get('operationCount') or 0) for e in evs),
            'life_start': min(times),
            'life_end': max(times),
            'events': len(evs),
        })
    return robots


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-09"
    ours_file = BASE / "data" / "replay_alor_history.jsonl"
    aniscan_file = BASE / "data" / f"aniscan_robots_{date}.jsonl"

    if not ours_file.exists():
        print(f"Нет файла {ours_file}: запусти end_of_day + replay_alor")
        return
    if not aniscan_file.exists():
        print(f"Нет файла {aniscan_file}")
        return

    events = load_jsonl(aniscan_file)
    robots = build_aniscan_robots(events)
    aniscan_dates = {datetime.fromtimestamp(r['life_start']).strftime('%Y-%m-%d')
                     for r in robots}

    all_ours = load_jsonl(ours_file)
    ours = [o for o in all_ours
            if (lambda st: st is not None and
                datetime.fromtimestamp(st).strftime('%Y-%m-%d') in aniscan_dates
                )(our_start_ts(o))]

    # только активные (<=60с) с обеих сторон — как в прошлых прогонах
    robots = [r for r in robots if 0 < r['period'] <= 60.0]
    ours = [o for o in ours if 0 < float(o.get('interval_avg') or 0) <= 60.0]

    print(f"[*] Aniscan: событий={len(events)} роботов-сущностей={len(robots)}")
    lifes = [r['life_end'] - r['life_start'] for r in robots]
    if lifes:
        lifes.sort()
        print(f"[*] жизнь робота: медиана={lifes[len(lifes)//2]:.0f}с "
              f"max={lifes[-1]:.0f}с; opCount медиана="
              f"{sorted(r['op_count'] for r in robots)[len(robots)//2]}")
    print(f"[*] наших серий (активных, за даты Aniscan): {len(ours)}")

    rob_by_key = defaultdict(list)
    for r in robots:
        rob_by_key[(r['ticker'], r['side'])].append(r)

    matched_rob = set()
    tp = 0
    fp_list = []
    for o in ours:
        st, en = our_start_ts(o), our_end_ts(o)
        if st is None or en is None:
            continue
        iv = float(o['interval_avg'])
        found = None
        for r in rob_by_key.get((o['symbol'], o['side'].upper()), []):
            if r['id'] in matched_rob:
                continue
            if abs(iv - r['period']) > max(2.0, r['period'] * 0.2):
                continue
            if en < r['life_start'] - GRACE_SEC or st > r['life_end'] + GRACE_SEC:
                continue
            found = r
            break
        if found:
            matched_rob.add(found['id'])
            tp += 1
        else:
            fp_list.append(o)

    fp = len(ours) - tp
    fn = len(robots) - len(matched_rob)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0

    print("\n" + "=" * 65)
    print(" 📊 v3: МАТЧИНГ ПО СУЩНОСТЯМ (окно серии внутри жизни робота)")
    print("=" * 65)
    print(f"Наших серий:   {len(ours)}")
    print(f"Роботов Aniscan: {len(robots)}")
    print("-" * 65)
    print(f"TP: {tp}   FP: {fp}   FN: {fn}")
    print(f"Precision: {prec:.2%} ({tp}/{tp + fp})")
    print(f"Recall:    {rec:.2%} ({tp}/{tp + fn})")
    print("=" * 65)

    if fp_list:
        print("\nFP (наши серии вне жизни любого робота Aniscan):")
        for o in fp_list[:10]:
            st = our_start_ts(o)
            tm = datetime.fromtimestamp(st).strftime('%H:%M:%S') if st else '?'
            print(f"  {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):.1f}s "
                  f"reps={o['repeats']} start={tm}")


if __name__ == "__main__":
    main()