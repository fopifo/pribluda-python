"""
GridLock — лот-агностичный детектор роботов по временной решётке.
Периодограмма (FFT-автокорреляция бинированных сделок) -> пики периода ->
уточнение фазы -> подсчёт попаданий в решётку (фон=ignore, пропуск=skip).
qty описательно (min/max). Работает даже когда робот — меньшинство сделок.
Требует numpy.
"""
import numpy as np


def detect(times, qtys, min_p=5.0, max_p=600.0, bin_s=1.0,
           min_count=15, min_reg=0.7, tol_ratio=0.15, top_k=8):
    t = np.asarray(times, dtype=float)
    q = np.asarray(qtys, dtype=float)
    if len(t) < min_count:
        return []
    order = np.argsort(t)
    t, q = t[order], q[order]
    t0, t1 = t[0], t[-1]
    L = int((t1 - t0) / bin_s) + 1
    if L < 4:
        return []
    idx = ((t - t0) / bin_s).astype(int)
    counts = np.bincount(idx, minlength=L).astype(float)

    n = 1 << (2 * L - 1).bit_length()
    f = np.fft.rfft(counts, n)
    ac = np.fft.irfft(f * np.conj(f), n)[:L]
    ac /= np.maximum(L - np.arange(L), 1)

    lo, hi = int(min_p / bin_s), min(int(max_p / bin_s), L - 2)
    if hi <= lo + 1:
        return []
    seg = ac[lo:hi + 1]
    floor = max(np.median(seg), 1e-9)
    peaks = []
    for i in range(1, len(seg) - 1):
        if seg[i] >= seg[i - 1] and seg[i] > seg[i + 1] and seg[i] > floor * 2:
            peaks.append((seg[i], lo + i))
    peaks.sort(reverse=True)

    kept = []
    out = []
    for score, lag in peaks[:top_k]:
        p = lag * bin_s
        tol = max(0.3, tol_ratio * p)
        ph = np.mod(t - t0, p)
        nb = max(1, int(p / tol))
        h = np.bincount((ph / tol).astype(int) % nb, minlength=nb)
        phase = int(np.argmax(h)) * tol
        d = np.abs(np.mod(t - t0 - phase + p / 2, p) - p / 2)
        aligned = d <= tol
        cnt = int(aligned.sum())
        if cnt < min_count:
            continue
        slots = (t1 - t0) / p
        reg = cnt / max(slots, 1)
        if reg < min_reg:
            continue
        aset = set(np.nonzero(aligned)[0].tolist())
        dup = False
        for kp, kset in kept:
            inter = len(aset & kset)
            if inter >= 0.8 * min(cnt, len(kset)) and p > kp:
                dup = True
                break
        if dup:
            continue
        kept.append((p, aset))
        at, aq = t[aligned], q[aligned]
        out.append({
            "interval_avg": round(p, 2),
            "repeats": cnt,
            "skips": int(max(slots - cnt, 0)),
            "regularity": round(reg, 2),
            "qty_variants": [int(aq.min()), int(aq.max())],
            "start_ts": float(at[0]), "end_ts": float(at[-1]),
            "start_ms": int(at[0] * 1000), "end_ms": int(at[-1] * 1000),
        })
    out.sort(key=lambda r: -r["repeats"])
    return out