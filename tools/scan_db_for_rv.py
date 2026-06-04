#!/usr/bin/env python3
"""
Scan the MIMIC-III waveform database for RV pressure morphology on the PAP
channel — i.e. likely PAC float ("advancement") moments.

Reuses the validated two-stage detector from rv_morphology_probe.py:
  - pulse-pressure gate rejects atrial (RA/wedge) traces
  - morphology (nadir position + late-diastolic slope) splits RV from PA

Strategy (keeps it cheap — see compute discussion):
  - For each master record, take its EARLIEST PAP-containing segment (the float
    appears when PAP first shows up), and fetch only the first few MINUTES of
    only the PAP channel from PhysioNet.
  - Slide a short window across it; flag records with RV-classified windows,
    and especially an RV->PA *step-up transition* (the strongest signal).

The mimic3wdb-matched waveform subset is OPEN access — no credentials needed.

Usage:
  python scan_db_for_rv.py --max-records 12
  python scan_db_for_rv.py --max-records 30 --shuffle
"""

import argparse
import json
import os
import random
import time

import numpy as np
import wfdb

from rv_morphology_probe import (
    beat_features, rv_likeness, classify, physiologic_ok, MIN_PLAUSIBLE_DIASTOLIC,
)

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "pap_records.json")
FETCH_MINUTES = 3.0      # only the first few minutes per segment
WIN_SEC = 8.0            # sliding analysis window
STEP_SEC = 4.0
# Temporal-coherence requirements (windows overlap: 8s window, 4s step).
MIN_RV_RUN = 3           # >=3 consecutive RV windows = a sustained RV passage
MIN_PA_RUN = 3           # >=3 consecutive PA windows = the post-float PA plateau
MAX_RUN_GAP = 2          # tolerate this many skipped windows between RV and PA


def earliest_pap_segment_per_record(catalog):
    """Group catalog segments by master record; return the earliest PAP
    segment (lowest segment_index) for each."""
    by_master = {}
    for s in catalog["segments"]:
        master = s["segment_name"].rsplit("_", 1)[0]
        cur = by_master.get(master)
        if cur is None or s["segment_index"] < cur["segment_index"]:
            by_master[master] = s
    return by_master


def clean_window(w):
    """Linear-interpolate small NaN gaps; reject windows that are mostly NaN."""
    if np.isnan(w).any():
        idx = np.arange(len(w))
        good = ~np.isnan(w)
        if good.sum() < 0.5 * len(w):
            return None
        w = np.interp(idx, idx[good], w[good])
    return w


def fetch_pap(seg):
    """Fetch the first FETCH_MINUTES of the PAP channel for one segment."""
    fs = seg["sampling_rate_hz"]
    ch = seg["pap_channels"][0]["channel_index"]
    sampto = min(seg["num_samples"], int(FETCH_MINUTES * 60 * fs))
    rec = wfdb.rdrecord(seg["segment_name"], pn_dir=seg["record_dir"],
                        channels=[ch], sampfrom=0, sampto=sampto)
    return rec.p_signal[:, 0], fs


def scan_segment(x, fs):
    """Slide the detector across a PAP trace, gating each window for
    physiologic plausibility. Returns an ordered sequence of windows:
    list of (t_sec, verdict, info) where verdict is 'RV', 'PA', or None
    (None = skipped: corrupted, missing, too few beats, or implausible)."""
    win, step = int(WIN_SEC * fs), int(STEP_SEC * fs)
    seq = []
    for start in range(0, max(1, len(x) - win), step):
        t = start / fs
        raw = x[start:start + win]
        if not physiologic_ok(raw, fs):           # gate 1: not corrupted/railing
            seq.append((t, None, None))
            continue
        w = clean_window(raw)
        if w is None:
            seq.append((t, None, None))
            continue
        r = rv_likeness(beat_features(w, fs))
        if r is None or r["n_beats"] < 3:
            seq.append((t, None, None))
            continue
        if r["median_diastolic"] < MIN_PLAUSIBLE_DIASTOLIC:   # gate 2: not negative
            seq.append((t, None, None))
            continue
        seq.append((t, classify(r), r))
    return seq


def _runs(verdicts):
    """Compress a verdict list into (value, start_idx, end_idx) runs."""
    out, i = [], 0
    while i < len(verdicts):
        v = verdicts[i]
        j = i
        while j < len(verdicts) and verdicts[j] == v:
            j += 1
        out.append((v, i, j))
        i = j
    return out


def analyze_runs(seq):
    """Apply temporal coherence: a real float is a SUSTAINED RV run, ideally
    followed by a SUSTAINED PA run (the step-up) — not 8s flip-flopping."""
    verdicts = [v for _, v, _ in seq]
    times = [t for t, _, _ in seq]
    runs = _runs(verdicts)

    longest_rv = max([e - s for v, s, e in runs if v == "RV"], default=0)

    transition, t_at = False, None
    for k, (v, s, e) in enumerate(runs):
        if v == "RV" and (e - s) >= MIN_RV_RUN:
            gap = 0
            for (v2, s2, e2) in runs[k + 1:]:
                if v2 is None:
                    gap += e2 - s2
                    if gap > MAX_RUN_GAP:
                        break
                    continue
                if v2 == "PA" and (e2 - s2) >= MIN_PA_RUN:
                    transition, t_at = True, times[s]
                break          # first substantive run after the RV run decides
            if transition:
                break

    rv_hits = [(t, info["score"], info["median_pp"])
               for t, v, info in seq if v == "RV"]
    best = max(rv_hits, key=lambda z: z[1]) if rv_hits else None
    return {
        "longest_rv_run": longest_rv,
        "n_rv_windows": len(rv_hits),
        "transition": transition,
        "transition_t": t_at,
        "best_rv": best,
    }


def main():
    ap = argparse.ArgumentParser(description="Scan MIMIC waveforms for RV/float")
    ap.add_argument("--max-records", type=int, default=12)
    ap.add_argument("--shuffle", action="store_true",
                    help="Randomize record order (default: catalog order)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    with open(CATALOG) as f:
        catalog = json.load(f)
    by_master = earliest_pap_segment_per_record(catalog)
    records = list(by_master.items())
    if args.shuffle:
        random.Random(args.seed).shuffle(records)
    records = records[: args.max_records]

    print("=" * 78)
    print(f"RV/float scan — {len(records)} records, first {FETCH_MINUTES:.0f} min "
          f"of earliest PAP segment each")
    print("=" * 78)

    candidates = []
    t_start = time.time()
    for i, (master, seg) in enumerate(records, 1):
        tag = f"[{i:>2}/{len(records)}] {master} {seg['segment_name']}"
        try:
            x, fs = fetch_pap(seg)
        except Exception as e:
            print(f"{tag}  fetch error: {e}")
            continue
        a = analyze_runs(scan_segment(x, fs))
        flagged = a["longest_rv_run"] >= MIN_RV_RUN     # sustained RV presence
        mark = ""
        if flagged and a["transition"]:
            mark = "  <<<< STRONG: SUSTAINED RV->PA TRANSITION"
        elif flagged:
            mark = "  <<< sustained RV run"
        best = a["best_rv"]
        best_str = (f" bestRV@{best[0]:.0f}s score={best[1]:.2f} PP={best[2]:.0f}"
                    if best else "")
        print(f"{tag}  rv_win={a['n_rv_windows']:>2} "
              f"rvRun={a['longest_rv_run']:>2}{best_str}{mark}")
        if flagged:
            candidates.append({
                "master_record": master,
                "segment_name": seg["segment_name"],
                "record_dir": seg["record_dir"],
                "n_rv_windows": a["n_rv_windows"],
                "longest_rv_run": a["longest_rv_run"],
                "transition": a["transition"],
                "transition_t": a["transition_t"],
                "best_rv": {"t_sec": best[0], "score": best[1], "pp": best[2]},
            })

    candidates.sort(key=lambda c: (c["transition"], c["longest_rv_run"]),
                    reverse=True)
    print("-" * 78)
    print(f"Scanned {len(records)} records in {time.time() - t_start:.0f}s. "
          f"Candidates flagged: {len(candidates)}")
    for c in candidates:
        kind = "TRANSITION" if c["transition"] else "RV-only"
        print(f"  * {c['master_record']} / {c['segment_name']}  "
              f"[{kind}]  rv_windows={c['n_rv_windows']}  "
              f"bestRV@{c['best_rv']['t_sec']:.0f}s score={c['best_rv']['score']:.2f}")
        print(f"      review: viewer record_dir={c['record_dir']} "
              f"seg={c['segment_name']} ~{c['best_rv']['t_sec']:.0f}s")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "rv_scan_results.json")
    with open(out, "w") as f:
        json.dump({"scanned": len(records), "candidates": candidates}, f, indent=2)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
