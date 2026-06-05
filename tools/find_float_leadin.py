#!/usr/bin/env python3
"""
Stage 2 of the wedge-anchored float finder: for each wedge anchor found by
find_wedge.py (wedge_candidates.json), look at the minutes BEFORE the wedge and
test for a real PAC float lead-in:  RA/IVC -> RV -> PA -> (wedge).

Why this works: most wedges in MIMIC are routine (catheter parked in PA for
hours, balloon inflated). Only an *insertion* float produces an RV->PA
diastolic step-up shortly before the wedge. So scoring the backward lead-in
filters the 59 anchors down to the rare complete floats.

Per-window chamber call reuses the validated RV-morphology detector:
  - LOWPP  : narrow pulse pressure  -> atrial (RA/IVC) or wedge
  - RV     : wide PP + RV morphology (low diastole, rising, no notch)
  - PA     : wide PP + PA morphology (dicrotic notch, runoff, higher diastole)
The float core is an RV run followed (closer to the wedge) by a PA run with a
diastolic STEP-UP; an RA (LOWPP) run before the RV adds the inflow stage.

Input : wedge_candidates.json  (from find_wedge.py)  + pap_records.json (channels)
Output: float_candidates.json   (ranked; does NOT touch wedge_candidates.json)

Usage:
  python find_float_leadin.py
  python find_float_leadin.py --lookback-min 12
"""

import argparse
import json
import os
import time

import numpy as np
import wfdb

from rv_morphology_probe import (
    beat_features, rv_likeness, classify, physiologic_ok, MIN_PLAUSIBLE_DIASTOLIC,
)

HERE = os.path.dirname(os.path.abspath(__file__))
WEDGE_PATH = os.path.join(HERE, "wedge_candidates.json")
CATALOG_PATH = os.path.join(HERE, "pap_records.json")
OUT_PATH = os.path.join(HERE, "float_candidates.json")

WIN_SEC = 8.0
STEP_SEC = 4.0
WEDGE_PP_MAX = 12.0      # narrow pulse pressure => atrial/wedge (LOWPP)
RV_RUN_MIN = 2           # consecutive RV windows for a real RV passage
PA_RUN_MIN = 2           # consecutive PA windows for the pre-wedge PA
DIA_STEPUP_MIN = 5.0     # RV->PA diastolic must rise at least this (mmHg)
LOOKBACK_MIN = 5.0       # minutes to fetch before the first flagged episode
FORWARD_MIN = 6.0        # minutes to fetch after the last flagged episode


def build_catalog_lookup():
    segs = json.load(open(CATALOG_PATH))["segments"]
    return {s["segment_name"]: s for s in segs}


def classify_window(w, fs):
    """Return ('RV'|'PA'|'LOWPP'|None, info) for one raw PAP window."""
    if not physiologic_ok(w, fs):
        return None, None
    r = rv_likeness(beat_features(w, fs))
    if r is None or r["n_beats"] < 3:
        return None, None
    if r["median_diastolic"] < MIN_PLAUSIBLE_DIASTOLIC:
        return None, None
    if r["median_pp"] < WEDGE_PP_MAX:
        return "LOWPP", r          # atrial (RA/IVC) or wedge
    return ("RV" if classify(r) == "RV" else "PA"), r


def _runs(verdicts):
    out, i = [], 0
    while i < len(verdicts):
        v = verdicts[i]
        j = i
        while j < len(verdicts) and verdicts[j] == v:
            j += 1
        out.append((v, i, j))
        i = j
    return out


def score_float_sequence(windows):
    """Search a classified window list (forward time order) for the ordered
    float pattern  RA(LOWPP) -> RV -> PA(step-up) -> wedge(LOWPP).

    RA and wedge are both LOWPP and disambiguated purely by POSITION (RA before
    the RV, wedge after the PA). Returns a dict with score and stage times."""
    verdicts = [v for _, v, _ in windows]
    runs = _runs(verdicts)
    rv_runs = [(s, e) for v, s, e in runs if v == "RV" and e - s >= RV_RUN_MIN]
    pa_runs = [(s, e) for v, s, e in runs if v == "PA" and e - s >= PA_RUN_MIN]
    lowpp_runs = [(s, e) for v, s, e in runs if v == "LOWPP"]

    def med_dia(s, e):
        return float(np.median([windows[i][2]["median_diastolic"]
                                for i in range(s, e)]))

    best = None
    for (rs, re) in rv_runs:
        for (ps, pe) in pa_runs:
            if ps < re:                       # PA must come AFTER the RV
                continue
            stepup = med_dia(ps, pe) - med_dia(rs, re)
            has_ra = any(le <= rs for (ls, le) in lowpp_runs)     # before RV
            has_wedge = any(ls >= pe for (ls, le) in lowpp_runs)  # after PA
            score = 2                         # RV + PA core
            if stepup >= DIA_STEPUP_MIN:
                score += 2                    # diastolic step-up (key signal)
            if has_ra:
                score += 1                    # RA/IVC inflow stage
            if has_wedge:
                score += 1                    # wedge after the PA
            cand = {
                "score": score, "rv": True, "pa": True,
                "ra": has_ra, "wedge_after": has_wedge,
                "dia_stepup": round(stepup, 1),
                "rv_t": round(windows[rs][0], 0),
                "pa_t": round(windows[ps][0], 0),
            }
            if best is None or score > best["score"]:
                best = cand
    if best:
        return best
    return {"score": 0, "rv": False, "pa": False, "ra": False,
            "wedge_after": False, "dia_stepup": 0.0, "rv_t": None, "pa_t": None}


def analyze_anchor(rec, catalog, lookback_s, forward_s):
    """Analyze one wedge-anchor segment. The flagged episode(s) just mark an
    interesting low-PP region; we fetch a window spanning before the first and
    after the last, then search the whole span for the ordered float sequence."""
    seg = catalog.get(rec["segment_name"])
    if seg is None:
        return None
    fs = seg["sampling_rate_hz"]
    ch = seg["pap_channels"][0]["channel_index"]
    ep_times = sorted(c["time_sec"] for c in rec["candidates"])

    start_s = max(0.0, ep_times[0] - lookback_s)
    end_s = min(seg["num_samples"] / fs, ep_times[-1] + forward_s)
    try:
        r = wfdb.rdrecord(seg["segment_name"], pn_dir=seg["record_dir"],
                          channels=[ch],
                          sampfrom=int(start_s * fs), sampto=int(end_s * fs))
    except Exception as e:
        return {"error": str(e)}
    x = r.p_signal[:, 0]

    win, step = int(WIN_SEC * fs), int(STEP_SEC * fs)
    seq = []
    for s in range(0, max(1, len(x) - win), step):
        v, info = classify_window(x[s:s + win], fs)
        seq.append((start_s + s / fs, v, info))

    seq = [w for w in seq if w[1] is not None]      # drop unclassifiable windows
    res = score_float_sequence(seq)
    res["wedge_t"] = ep_times[-1]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-min", type=float, default=LOOKBACK_MIN)
    ap.add_argument("--forward-min", type=float, default=FORWARD_MIN)
    ap.add_argument("--max-anchors", type=int, default=0)
    args = ap.parse_args()
    lookback_s = args.lookback_min * 60.0
    forward_s = args.forward_min * 60.0

    anchors = json.load(open(WEDGE_PATH))
    catalog = build_catalog_lookup()
    if args.max_anchors:
        anchors = anchors[: args.max_anchors]

    print("=" * 80)
    print(f"Stage 2: float lead-in for {len(anchors)} wedge anchors "
          f"(lookback {args.lookback_min:.0f} min)")
    print("=" * 80)

    results = []
    t0 = time.time()
    for i, rec in enumerate(anchors, 1):
        res = analyze_anchor(rec, catalog, lookback_s, forward_s)
        if res is None or "error" in res:
            print(f"[{i:>2}/{len(anchors)}] {rec['patient_id']} / "
                  f"{rec['segment_name']}  -- skipped")
            continue
        stages = "".join([
            "R" if res["ra"] else "-",
            "V" if res["rv"] else "-",
            "P" if res["pa"] else "-",
            "W" if res["wedge_after"] else "-",
        ])
        flag = "  <<<< COMPLETE FLOAT" if res["score"] >= 6 else (
            "  <<< RV->PA" if res["rv"] else "")
        print(f"[{i:>2}/{len(anchors)}] {rec['patient_id']:>9} / "
              f"{rec['segment_name']:<14} float_score={res['score']} "
              f"[{stages}] stepup={res['dia_stepup']}{flag}")
        res.update({
            "patient_id": rec["patient_id"],
            "segment_name": rec["segment_name"],
            "record_dir": rec["record_dir"],
            "wedge_best_score": rec["best_score"],
        })
        results.append(res)

    results.sort(key=lambda r: (r["score"], r["dia_stepup"],
                                r["wedge_best_score"]), reverse=True)
    print("-" * 80)
    print(f"Analyzed {len(results)} anchors in {time.time() - t0:.0f}s. "
          f"Complete floats (score>=6): "
          f"{sum(1 for r in results if r['score'] >= 6)}")
    print("\nTOP candidates:")
    for r in results[:12]:
        print(f"  {r['patient_id']:>9} / {r['segment_name']:<14} "
              f"score={r['score']} stepup={r['dia_stepup']} "
              f"RA={r['ra']} RV={r['rv']} wedge@{r['wedge_t']:.0f}s "
              f"RV@{r['rv_t']} PA@{r['pa_t']}")

    json.dump({"results": results}, open(OUT_PATH, "w"), indent=2)
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
