#!/usr/bin/env python3
"""
Export a waveform case from MIMIC-III for use in the PAC Simulator.

Downloads a specified time window from a PhysioNet segment and saves each
signal as a simple CSV file in waveform_data/{case_name}/.  The resulting
files are self-contained — the simulator never needs internet access.

Usage examples:
  # Export Bookmark 1 — normal sinus rhythm
  python export_waveform_case.py \
      --segment 3027112_0001 \
      --record-dir mimic3wdb-matched/1.0/p00/p000214 \
      --start 4500 \
      --duration 120 \
      --case normal_sinus \
      --description "Normal sinus rhythm, normal arterial and PA pressures"

  # Export with default 2-minute duration
  python export_waveform_case.py \
      --segment 3544749_0005 \
      --record-dir mimic3wdb-matched/1.0/p00/p000020 \
      --start 0 \
      --case atrial_paced
"""

import argparse
import csv
import json
import os
import sys

import numpy as np

try:
    import wfdb
except ImportError:
    print("ERROR: wfdb library not installed.  Run:  pip install wfdb")
    sys.exit(1)


# Signals we care about for the simulator (mapped to display names)
# The key is the MIMIC signal label; the value is the filename stem we save.
SIGNAL_MAP = {
    # ECG leads — we prefer Lead II but accept others
    "II": "II",
    "I": "I",
    "III": "III",
    "V": "V",
    "AVR": "AVR",
    "AVL": "AVL",
    "AVF": "AVF",
    "MCL": "MCL",
    "MCL1": "MCL1",
    # Arterial blood pressure
    "ABP": "ABP",
    "ART": "ABP",      # alias
    "AOBP": "ABP",     # alias
    # Pulmonary artery pressure
    "PAP": "PAP",
    # Central venous pressure
    "CVP": "CVP",
    # Plethysmograph
    "PLETH": "PLETH",
    # Respiration
    "RESP": "RESP",
}

# Which signals the simulator currently supports displaying
SIMULATOR_SIGNALS = {"II", "ABP", "PAP", "CVP"}


def download_window(segment_name, record_dir, start_sec, duration_sec):
    """Download a time window from PhysioNet and return the record object."""
    print(f"Downloading from PhysioNet...")
    print(f"  Segment : {segment_name}")
    print(f"  Record  : {record_dir}")

    # First read the header to get sampling rate and total length
    header = wfdb.rdheader(segment_name, pn_dir=record_dir)
    fs = header.fs
    total_samples = header.sig_len

    sampfrom = int(start_sec * fs)
    sampto = int((start_sec + duration_sec) * fs)

    # Clamp to valid range
    sampfrom = max(0, min(sampfrom, total_samples - 1))
    sampto = max(sampfrom + 1, min(sampto, total_samples))

    actual_duration = (sampto - sampfrom) / fs
    print(f"  Fs      : {fs} Hz")
    print(f"  Samples : {sampfrom} to {sampto} ({sampto - sampfrom} samples)")
    print(f"  Duration: {actual_duration:.1f} seconds")
    print(f"  Signals : {header.sig_name}")

    record = wfdb.rdrecord(
        segment_name,
        pn_dir=record_dir,
        sampfrom=sampfrom,
        sampto=sampto,
    )
    return record


def save_case(record, case_name, segment_name, record_dir, start_sec,
              duration_sec, description=""):
    """Save each signal as a separate CSV in waveform_data/{case_name}/."""
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "waveform_data", case_name)
    os.makedirs(base_dir, exist_ok=True)

    fs = record.fs
    sig_names = record.sig_name
    units = record.units
    data = record.p_signal  # numpy array: (num_samples, num_channels)
    num_samples = data.shape[0]

    saved_signals = []
    files_written = []

    for ch_idx, sig_label in enumerate(sig_names):
        # Map to our standard name (or skip if unknown)
        if sig_label not in SIGNAL_MAP:
            print(f"  Skipping unknown signal: {sig_label}")
            continue

        out_name = SIGNAL_MAP[sig_label]
        channel_data = data[:, ch_idx]

        # Check for NaN/invalid data
        nan_count = np.isnan(channel_data).sum()
        if nan_count == num_samples:
            print(f"  Skipping {sig_label}: all NaN")
            continue
        elif nan_count > 0:
            # Interpolate small gaps
            print(f"  {sig_label}: interpolating {nan_count} NaN values "
                  f"({nan_count/num_samples*100:.1f}%)")
            mask = np.isnan(channel_data)
            channel_data[mask] = np.interp(
                np.flatnonzero(mask),
                np.flatnonzero(~mask),
                channel_data[~mask]
            )

        # Save as simple CSV: one column, with a header row
        csv_path = os.path.join(base_dir, f"{out_name}.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([f"{out_name} ({units[ch_idx]})"])
            for val in channel_data:
                writer.writerow([f"{val:.4f}"])

        files_written.append(csv_path)
        saved_signals.append({
            "signal_name": out_name,
            "original_label": sig_label,
            "units": units[ch_idx],
            "num_samples": num_samples,
            "file": f"{out_name}.csv",
        })

        in_sim = " [SIMULATOR]" if out_name in SIMULATOR_SIGNALS else ""
        print(f"  Saved {sig_label} -> {out_name}.csv "
              f"({num_samples} samples, {units[ch_idx]}){in_sim}")

    # Extract patient ID from record_dir
    parts = record_dir.rstrip("/").split("/")
    patient_id = parts[-1] if parts else "unknown"

    # Write metadata
    metadata = {
        "case_name": case_name,
        "description": description,
        "source": {
            "database": "mimic3wdb-matched/1.0",
            "patient_id": patient_id,
            "segment_name": segment_name,
            "record_dir": record_dir,
            "start_seconds": start_sec,
            "duration_seconds": duration_sec,
        },
        "sampling_rate_hz": fs,
        "num_samples": num_samples,
        "signals": saved_signals,
    }

    meta_path = os.path.join(base_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    files_written.append(meta_path)

    print(f"\nCase '{case_name}' saved to: {base_dir}")
    print(f"  {len(saved_signals)} signals, {num_samples} samples "
          f"({num_samples/fs:.1f}s at {fs} Hz)")
    print(f"  Metadata: {meta_path}")

    # Show which signals the simulator will use
    sim_signals = [s for s in saved_signals
                   if s["signal_name"] in SIMULATOR_SIGNALS]
    if sim_signals:
        print(f"\n  Simulator-compatible signals:")
        for s in sim_signals:
            print(f"    - {s['signal_name']} ({s['units']})")

    return base_dir


def main():
    parser = argparse.ArgumentParser(
        description="Export a waveform case from MIMIC-III for the PAC Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--segment", required=True,
                        help="Segment name (e.g., 3027112_0001)")
    parser.add_argument("--record-dir", required=True,
                        help="PhysioNet record directory "
                             "(e.g., mimic3wdb-matched/1.0/p00/p000214)")
    parser.add_argument("--start", type=float, default=0,
                        help="Start time in seconds (default: 0)")
    parser.add_argument("--duration", type=float, default=120,
                        help="Duration in seconds (default: 120 = 2 minutes)")
    parser.add_argument("--case", required=True,
                        help="Case name for the output folder "
                             "(e.g., normal_sinus)")
    parser.add_argument("--description", default="",
                        help="Optional description of the clinical scenario")

    args = parser.parse_args()

    print("=" * 60)
    print("PAC Simulator — Waveform Case Exporter")
    print("=" * 60)

    record = download_window(
        args.segment, args.record_dir, args.start, args.duration
    )
    save_case(
        record, args.case, args.segment, args.record_dir,
        args.start, args.duration, args.description
    )

    print("\nDone!")


if __name__ == "__main__":
    main()
