"""
Wedge Pressure Finder
=====================
Scans PAP segments from pap_records.json looking for brief episodes of
pulmonary capillary wedge pressure (PCWP).

Strategy:
  A wedge tracing appears as a brief (5-30s) dampening of the PA waveform:
    - Lower mean pressure than surrounding PA
    - Much lower pulse pressure (pulsatility) than surrounding PA
    - Bounded on at least one side by normal PA morphology

  We slide a window across each segment's PAP channel and score each window
  for "wedge-ness", then look for transient dips in pulsatility surrounded
  by higher-pulsatility PA signal.

Usage:
    python find_wedge.py                    # scan all segments
    python find_wedge.py --max-segments 50  # limit for testing
    python find_wedge.py --resume           # skip already-scanned segments
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import wfdb

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "pap_records.json")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "wedge_candidates.json")
PROGRESS_PATH = os.path.join(os.path.dirname(__file__), "wedge_scan_progress.json")

# -- Tunable parameters ---------------------------------------------------
CHUNK_DURATION_SEC = 300       # Download 5 minutes at a time
WINDOW_SEC = 8                 # Sliding window size for stats
STEP_SEC = 4                   # Step between windows
MIN_PA_PULSE_PRESSURE = 10     # mmHg -- minimum pulse pressure to consider "PA"
MAX_WEDGE_PULSE_PRESSURE = 12  # mmHg -- wedge should have dampened pulsatility
MIN_WEDGE_MEAN = 4             # mmHg -- wedge mean should be above this
MAX_WEDGE_MEAN = 22            # mmHg -- wedge mean should be below this
MIN_WEDGE_DURATION_SEC = 4     # Wedge episode must last at least this long
MAX_WEDGE_DURATION_SEC = 60    # Wedge episode shouldn't last longer than this
PULSATILITY_RATIO_THRESH = 0.5 # Wedge pulse pressure should be < 50% of surrounding PA


def load_catalog():
    with open(CATALOG_PATH) as f:
        return json.load(f)


def load_progress():
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH) as f:
            return set(json.load(f))
    return set()


def save_progress(scanned_set):
    with open(PROGRESS_PATH, "w") as f:
        json.dump(sorted(scanned_set), f)


def save_results(candidates):
    with open(RESULTS_PATH, "w") as f:
        json.dump(candidates, f, indent=2)
    print(f"\n  Results saved to {RESULTS_PATH}")


def fetch_chunk(segment_name, record_dir, sampfrom, sampto, channels=None):
    """Download a chunk of waveform data from PhysioNet.

    Pass channels=[idx] to fetch only the PAP channel (much less download
    than pulling every co-recorded signal we don't use here).
    """
    try:
        record = wfdb.rdrecord(
            segment_name,
            pn_dir=record_dir,
            sampfrom=sampfrom,
            sampto=sampto,
            channels=channels,
        )
        return record
    except Exception as e:
        print(f"    WARNING: Download error: {e}")
        return None


def cardiac_regularity_score(window, fs):
    """Score how much a signal looks like organized cardiac pulsatility (0-1).

    Uses autocorrelation to detect a regular repeating pattern at heart-rate
    frequencies (40-180 bpm = period 0.33-1.5 seconds).

    Returns:
        float 0-1, where >0.3 suggests organized cardiac rhythm,
        <0.15 suggests noise/artifact.
    """
    # Subtract mean
    x = window - np.mean(window)
    norm = np.sum(x ** 2)
    if norm < 1e-6:
        return 0.0

    # Autocorrelation via numpy
    autocorr = np.correlate(x, x, mode='full')
    autocorr = autocorr[len(autocorr) // 2:]  # keep positive lags only
    autocorr = autocorr / autocorr[0]  # normalize

    # Look for peak in heart-rate range: 40-180 bpm
    min_lag = int(fs * 60 / 180)  # ~0.33s at 180bpm
    max_lag = int(fs * 60 / 40)   # ~1.5s at 40bpm
    max_lag = min(max_lag, len(autocorr) - 1)

    if min_lag >= max_lag:
        return 0.0

    search_region = autocorr[min_lag:max_lag + 1]
    peak_val = float(np.max(search_region))

    return max(0.0, peak_val)


def compute_window_stats(pap_signal, fs, window_sec=WINDOW_SEC, step_sec=STEP_SEC):
    """Compute sliding-window statistics for a PAP signal.

    Returns list of dicts with: offset_sec, mean, std, pulse_pressure,
    regularity (autocorrelation-based cardiac rhythm score).
    """
    window_samples = int(window_sec * fs)
    step_samples = int(step_sec * fs)
    stats = []

    for start in range(0, len(pap_signal) - window_samples, step_samples):
        window = pap_signal[start:start + window_samples]

        # Skip windows with NaN or all-zero
        if np.any(np.isnan(window)) or np.std(window) < 0.1:
            continue

        mean_p = float(np.mean(window))
        std_p = float(np.std(window))

        # Estimate pulse pressure: use percentiles to be robust to noise
        p95 = float(np.percentile(window, 95))
        p5 = float(np.percentile(window, 5))
        pulse_pressure = p95 - p5

        # Cardiac regularity -- distinguishes real signal from artifact/noise
        regularity = cardiac_regularity_score(window, fs)

        stats.append({
            "sample_offset": start,
            "offset_sec": start / fs,
            "mean": round(mean_p, 1),
            "std": round(std_p, 2),
            "pulse_pressure": round(pulse_pressure, 1),
            "p95": round(p95, 1),
            "p5": round(p5, 1),
            "regularity": round(regularity, 3),
        })

    return stats


def find_wedge_episodes(window_stats, chunk_offset_sec=0):
    """Look for transient drops in pulsatility consistent with wedge pressure.

    A wedge episode is:
    1. A run of consecutive windows with low pulse pressure AND mean in wedge range
    2. Preceded or followed by windows with higher pulse pressure (normal PA)
    3. Duration between MIN_WEDGE_DURATION and MAX_WEDGE_DURATION

    Returns list of candidate episodes.
    """
    if len(window_stats) < 5:
        return []

    candidates = []

    MIN_REGULARITY_PA = 0.25    # PA context must show organized cardiac rhythm
    MIN_REGULARITY_WEDGE = 0.15  # Wedge can be less pulsatile but not random noise

    # Label each window as "wedge-like" or "PA-like"
    for i, w in enumerate(window_stats):
        w["is_wedge_like"] = (
            w["pulse_pressure"] < MAX_WEDGE_PULSE_PRESSURE
            and MIN_WEDGE_MEAN < w["mean"] < MAX_WEDGE_MEAN
            and w["regularity"] >= MIN_REGULARITY_WEDGE  # must be cardiac, not noise
        )
        w["is_pa_like"] = (
            w["pulse_pressure"] >= MIN_PA_PULSE_PRESSURE
            and w["regularity"] >= MIN_REGULARITY_PA  # must be clean PA, not artifact
        )

    # Find runs of wedge-like windows
    i = 0
    while i < len(window_stats):
        if not window_stats[i]["is_wedge_like"]:
            i += 1
            continue

        # Start of a potential wedge run
        run_start = i
        while i < len(window_stats) and window_stats[i]["is_wedge_like"]:
            i += 1
        run_end = i  # exclusive

        # Duration of the wedge run
        start_sec = window_stats[run_start]["offset_sec"]
        end_sec = window_stats[run_end - 1]["offset_sec"] + WINDOW_SEC
        duration = end_sec - start_sec

        if duration < MIN_WEDGE_DURATION_SEC or duration > MAX_WEDGE_DURATION_SEC:
            continue

        # REQUIRE clean PA on BOTH sides (classic PA -> wedge -> PA)
        # A real wedge is always a brief interruption of normal PA
        context_range = 5
        before_pa = any(
            window_stats[j]["is_pa_like"]
            for j in range(max(0, run_start - context_range), run_start)
        )
        after_pa = any(
            window_stats[j]["is_pa_like"]
            for j in range(run_end, min(len(window_stats), run_end + context_range))
        )

        if not (before_pa and after_pa):
            continue  # Must have clean PA on BOTH sides

        # Compute pulsatility ratio vs surrounding PA
        context_pp = []
        context_reg = []
        for j in range(max(0, run_start - context_range), run_start):
            if window_stats[j]["is_pa_like"]:
                context_pp.append(window_stats[j]["pulse_pressure"])
                context_reg.append(window_stats[j]["regularity"])
        for j in range(run_end, min(len(window_stats), run_end + context_range)):
            if window_stats[j]["is_pa_like"]:
                context_pp.append(window_stats[j]["pulse_pressure"])
                context_reg.append(window_stats[j]["regularity"])

        if not context_pp:
            continue

        mean_context_pp = np.mean(context_pp)
        mean_context_reg = np.mean(context_reg)
        wedge_pp = np.mean([window_stats[j]["pulse_pressure"]
                            for j in range(run_start, run_end)])
        wedge_reg = np.mean([window_stats[j]["regularity"]
                             for j in range(run_start, run_end)])
        ratio = wedge_pp / mean_context_pp if mean_context_pp > 0 else 1.0

        # The pulsatility must actually DROP significantly
        if ratio > PULSATILITY_RATIO_THRESH:
            continue

        wedge_mean = np.mean([window_stats[j]["mean"]
                              for j in range(run_start, run_end)])

        # Score: lower ratio = more dampened = more likely wedge
        score = (1.0 - ratio) * 100  # 0-100, higher = better candidate

        # Bonus for mean being in sweet spot (8-15 mmHg)
        if 8 <= wedge_mean <= 15:
            score += 10

        # Bonus for high regularity in both PA context and wedge
        if mean_context_reg > 0.4:
            score += 10
        if wedge_reg > 0.25:
            score += 5

        if score > 30:  # Raised threshold -- be pickier
            candidates.append({
                "time_sec": round(chunk_offset_sec + start_sec, 1),
                "time_human": format_time(chunk_offset_sec + start_sec),
                "duration_sec": round(duration, 1),
                "wedge_mean_mmhg": round(float(wedge_mean), 1),
                "wedge_pulse_pressure": round(float(wedge_pp), 1),
                "context_pulse_pressure": round(float(mean_context_pp), 1),
                "pulsatility_ratio": round(float(ratio), 2),
                "wedge_regularity": round(float(wedge_reg), 3),
                "context_regularity": round(float(mean_context_reg), 3),
                "pa_before": before_pa,
                "pa_after": after_pa,
                "score": round(score, 1),
            })

    return candidates


def format_time(seconds):
    """Format seconds as H:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"


def scan_segment(seg, seg_num, total_segs):
    """Scan a single segment for wedge episodes."""
    segment_name = seg["segment_name"]
    record_dir = seg["record_dir"]
    fs = seg["sampling_rate_hz"]
    total_samples = seg["num_samples"]
    total_sec = seg["duration_sec"]
    pap_idx = seg["pap_channels"][0]["channel_index"]

    patient_id = record_dir.rstrip("/").split("/")[-1]
    print(f"\n[{seg_num}/{total_segs}] {patient_id} / {segment_name} "
          f"({format_time(total_sec)}, {total_sec/60:.0f} min)")

    chunk_samples = int(CHUNK_DURATION_SEC * fs)
    all_candidates = []

    chunk_num = 0
    total_chunks = (total_samples + chunk_samples - 1) // chunk_samples

    for chunk_start in range(0, total_samples, chunk_samples):
        chunk_num += 1
        chunk_end = min(chunk_start + chunk_samples, total_samples)
        chunk_offset_sec = chunk_start / fs

        # Progress indicator
        pct = chunk_start / total_samples * 100
        print(f"  Chunk {chunk_num}/{total_chunks} "
              f"({format_time(chunk_offset_sec)} - {format_time(chunk_end/fs)}) "
              f"[{pct:.0f}%]", end="")

        record = fetch_chunk(segment_name, record_dir, chunk_start, chunk_end,
                             channels=[pap_idx])
        if record is None:
            print(" X")
            continue

        # PAP-only fetch -> the single channel is at column 0
        try:
            pap_signal = record.p_signal[:, 0]
        except (IndexError, AttributeError):
            print(" X (bad signal)")
            continue

        # Quick check: skip if entire chunk is flat or NaN
        valid = pap_signal[~np.isnan(pap_signal)]
        if len(valid) < fs * 10 or np.std(valid) < 0.5:
            print(" - flat/empty")
            continue

        # Compute window stats and search for wedge episodes
        stats = compute_window_stats(pap_signal, fs)
        candidates = find_wedge_episodes(stats, chunk_offset_sec)

        if candidates:
            print(f" ** {len(candidates)} candidate(s)!")
            for c in candidates:
                print(f"    -> {c['time_human']} ({c['duration_sec']}s) "
                      f"mean={c['wedge_mean_mmhg']}mmHg "
                      f"PP={c['wedge_pulse_pressure']}->{c['context_pulse_pressure']}mmHg "
                      f"score={c['score']}")
        else:
            print(" .")

        all_candidates.extend(candidates)

        # Small delay to be polite to PhysioNet
        time.sleep(0.1)

    return all_candidates


def main():
    parser = argparse.ArgumentParser(description="Scan PAP segments for wedge pressure episodes")
    parser.add_argument("--max-segments", type=int, default=0,
                        help="Max segments to scan (0 = all)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip segments already scanned")
    parser.add_argument("--min-duration", type=float, default=5.0,
                        help="Min segment duration in minutes (skip short ones)")
    args = parser.parse_args()

    catalog = load_catalog()
    segments = catalog["segments"]
    scanned = load_progress() if args.resume else set()

    # Load existing results if resuming
    if args.resume and os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH) as f:
            all_results = json.load(f)
    else:
        all_results = []

    # Filter and sort segments
    segments = [s for s in segments
                if s["duration_min"] >= args.min_duration
                and s["segment_name"] not in scanned]

    # Sort by duration ascending -- shorter segments are faster to scan
    # and wedge is more likely to be a notable fraction of a shorter recording
    segments.sort(key=lambda s: s["duration_sec"])

    if args.max_segments > 0:
        segments = segments[:args.max_segments]

    total = len(segments)
    print(f"Wedge Pressure Finder")
    print(f"=====================")
    print(f"Segments to scan: {total}")
    if scanned:
        print(f"Previously scanned: {len(scanned)} (resuming)")
    print()

    try:
        for i, seg in enumerate(segments, 1):
            candidates = scan_segment(seg, i, total)

            # Record progress
            scanned.add(seg["segment_name"])
            save_progress(scanned)

            if candidates:
                patient_id = seg["record_dir"].rstrip("/").split("/")[-1]
                result = {
                    "patient_id": patient_id,
                    "segment_name": seg["segment_name"],
                    "record_dir": seg["record_dir"],
                    "segment_duration_min": round(seg["duration_min"], 1),
                    "candidates": candidates,
                    "best_score": max(c["score"] for c in candidates),
                }
                all_results.append(result)
                # Save after each segment with hits
                save_results(all_results)

    except KeyboardInterrupt:
        print("\n\nInterrupted -- saving progress...")
        save_progress(scanned)
        save_results(all_results)

    # Final summary
    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE")
    print(f"Segments scanned: {len(scanned)}")
    print(f"Segments with candidates: {len(all_results)}")

    if all_results:
        # Sort by best score
        all_results.sort(key=lambda r: r["best_score"], reverse=True)
        save_results(all_results)

        print(f"\nTop candidates:")
        for r in all_results[:15]:
            best = max(r["candidates"], key=lambda c: c["score"])
            print(f"  {r['patient_id']} / {r['segment_name']} "
                  f"@ {best['time_human']} "
                  f"(score={best['score']}, "
                  f"mean={best['wedge_mean_mmhg']}mmHg, "
                  f"PP {best['wedge_pulse_pressure']}->{best['context_pulse_pressure']}mmHg)")
    else:
        print("\nNo wedge candidates found yet.")


if __name__ == "__main__":
    main()
