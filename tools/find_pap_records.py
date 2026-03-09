"""
Phase 1: Scan the MIMIC-III Waveform Database Matched Subset for records
containing Pulmonary Artery Pressure (PAP) waveforms.

This script reads ONLY header files (lightweight metadata) from PhysioNet
to identify which patient records and segments contain PAP signals,
without downloading any large binary data files.

Uses a two-pass approach:
  Pass 1 (fast): Read layout headers to identify records that contain PAP
                  in ANY segment. This skips the majority of records quickly.
  Pass 2 (targeted): For records with PAP, scan individual segments to
                      find exactly which ones have PAP and for how long.

Results are saved to a JSON file for use by the extraction script.
"""

import json
import time
import argparse
from pathlib import Path
from datetime import datetime

import requests
import wfdb
from tqdm import tqdm

DATABASE = "mimic3wdb-matched/1.0"
PAP_LABELS = ["PAP"]  # The waveform signal label for pulmonary artery pressure
OUTPUT_FILE = "pap_records.json"


def get_waveform_record_paths():
    """
    Retrieve the list of all waveform record paths from RECORDS-waveforms.

    Returns paths like: "p00/p000020/p000020-2183-04-28-17-47"
    """
    print("Fetching waveform record list from PhysioNet...")
    url = f"https://physionet.org/files/{DATABASE}/RECORDS-waveforms"
    resp = requests.get(url)
    resp.raise_for_status()
    records = [line.strip() for line in resp.text.strip().split("\n") if line.strip()]
    print(f"  Total waveform records: {len(records)}")
    return records


def check_layout_for_pap(record_path):
    """
    Quick check: does the layout header for this record mention PAP?

    The layout header lists ALL signals that appear in ANY segment of the
    record, so this is a fast way to skip records that never had PAP.

    Returns the layout signal names if PAP is found, else None.
    """
    parts = record_path.rsplit("/", 1)
    record_dir = f"{DATABASE}/{parts[0]}"
    record_name = parts[1]

    try:
        master = wfdb.rdheader(record_name, pn_dir=record_dir)
    except Exception:
        return None

    seg_names = getattr(master, "seg_name", None)
    if not seg_names:
        return None

    # Find and read the layout segment (contains '_layout' in name)
    for seg in seg_names:
        if seg == "~":
            continue
        if "_layout" in seg:
            try:
                layout_hdr = wfdb.rdheader(seg, pn_dir=record_dir)
                if layout_hdr.sig_name and any(
                    s in PAP_LABELS for s in layout_hdr.sig_name
                ):
                    return layout_hdr.sig_name
            except Exception:
                pass
            break

    return None


def scan_segments_for_pap(record_path, min_duration_sec=60):
    """
    For a record known to contain PAP, scan individual segments to find
    which ones have PAP and meet the minimum duration.

    Returns list of dicts describing matching segments.
    """
    parts = record_path.rsplit("/", 1)
    record_dir = f"{DATABASE}/{parts[0]}"
    record_name = parts[1]

    matches = []

    try:
        master = wfdb.rdheader(record_name, pn_dir=record_dir)
    except Exception:
        return matches

    seg_names = getattr(master, "seg_name", [])
    if not seg_names:
        return matches

    for i, seg_name in enumerate(seg_names):
        if seg_name == "~" or "_layout" in seg_name:
            continue

        try:
            seg_hdr = wfdb.rdheader(seg_name, pn_dir=record_dir)
        except Exception:
            continue

        if not seg_hdr.sig_name:
            continue

        pap_signals_found = [s for s in seg_hdr.sig_name if s in PAP_LABELS]
        if not pap_signals_found:
            continue

        duration_sec = seg_hdr.sig_len / seg_hdr.fs if seg_hdr.fs else 0
        if duration_sec < min_duration_sec:
            continue

        pap_channels = []
        for sig_label in pap_signals_found:
            idx = seg_hdr.sig_name.index(sig_label)
            pap_channels.append(
                {
                    "label": sig_label,
                    "channel_index": idx,
                    "units": seg_hdr.units[idx] if seg_hdr.units else "unknown",
                }
            )

        matches.append(
            {
                "record_path": record_path,
                "record_dir": record_dir,
                "segment_name": seg_name,
                "segment_index": i,
                "all_signals": seg_hdr.sig_name,
                "pap_channels": pap_channels,
                "sampling_rate_hz": seg_hdr.fs,
                "num_samples": seg_hdr.sig_len,
                "duration_sec": round(duration_sec, 1),
                "duration_min": round(duration_sec / 60, 1),
            }
        )

    return matches


def main():
    parser = argparse.ArgumentParser(
        description="Scan MIMIC-III waveform database for PAP records"
    )
    parser.add_argument(
        "--min-duration",
        type=int,
        default=60,
        help="Minimum segment duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="Max records to scan (0 = all, useful for testing)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=OUTPUT_FILE,
        help=f"Output JSON file (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    start_time = time.time()

    # Get list of all waveform records
    all_records = get_waveform_record_paths()

    if args.max_records > 0:
        all_records = all_records[: args.max_records]
        print(f"  Limiting scan to first {args.max_records} records (test mode)")

    # --- Pass 1: Quick layout scan to find records with PAP ---
    print(f"\nPass 1: Scanning {len(all_records)} layout headers for PAP...")
    pap_records = []
    pass1_errors = 0

    for record_path in tqdm(all_records, desc="Pass 1 (layout scan)"):
        try:
            layout_sigs = check_layout_for_pap(record_path)
            if layout_sigs:
                pap_records.append(record_path)
                tqdm.write(
                    f"  FOUND PAP: {record_path} (signals: {layout_sigs})"
                )
        except Exception:
            pass1_errors += 1

    pass1_time = time.time() - start_time
    print(f"\nPass 1 complete: {len(pap_records)} records with PAP "
          f"(scanned {len(all_records)} in {pass1_time:.1f}s)")

    if not pap_records:
        print("\nNo records with PAP found.")
        output = {
            "scan_timestamp": datetime.now().isoformat(),
            "database": DATABASE,
            "min_duration_sec": args.min_duration,
            "records_scanned": len(all_records),
            "records_with_pap": 0,
            "total_pap_segments": 0,
            "segments": [],
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        return

    # --- Pass 2: Detailed segment scan for PAP records ---
    print(f"\nPass 2: Scanning segments in {len(pap_records)} PAP records...")
    all_matches = []

    for record_path in tqdm(pap_records, desc="Pass 2 (segment scan)"):
        try:
            matches = scan_segments_for_pap(record_path, args.min_duration)
            all_matches.extend(matches)
            for m in matches:
                tqdm.write(
                    f"  Segment {m['segment_name']}: "
                    f"{m['duration_min']:.1f} min, "
                    f"signals={m['all_signals']}"
                )
        except Exception:
            pass

    elapsed = time.time() - start_time

    # Summary
    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE")
    print(f"{'='*60}")
    print(f"  Records scanned:      {len(all_records)}")
    print(f"  Records with PAP:     {len(pap_records)}")
    print(f"  PAP segments (>={args.min_duration}s): {len(all_matches)}")
    print(f"  Pass 1 errors:        {pass1_errors}")
    print(f"  Elapsed time:         {elapsed:.1f}s")

    if all_matches:
        durations = [m["duration_min"] for m in all_matches]
        print(f"\n  PAP segment durations:")
        print(f"    Shortest: {min(durations):.1f} min")
        print(f"    Longest:  {max(durations):.1f} min")
        print(f"    Total:    {sum(durations):.1f} min ({sum(durations)/60:.1f} hrs)")

    # Save results
    output = {
        "scan_timestamp": datetime.now().isoformat(),
        "database": DATABASE,
        "min_duration_sec": args.min_duration,
        "records_scanned": len(all_records),
        "records_with_pap": len(pap_records),
        "total_pap_segments": len(all_matches),
        "segments": all_matches,
    }

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
