#!/usr/bin/env python3
"""
Align PAC insertion timestamps with waveform segment timing.

Reads the master record header from PhysioNet to find each segment's
start time, then calculates which segment (and what offset within it)
corresponds to the PAC insertion procedure time.
"""

import json
import os
from datetime import datetime, timedelta

import wfdb


def get_record_layout(record_dir):
    """Read the master record header to get segment timing info."""
    # The master record name is the last part of the record_dir path
    # e.g., "mimic3wdb-matched/1.0/p00/p006917" -> record "3187965"
    # But we need the actual record name from the directory listing.
    # Let's read the record header which lists all segments.

    # Get the record name from the directory
    parts = record_dir.rstrip("/").split("/")
    patient_dir = "/".join(parts)

    # Read the RECORDS file to get record names
    print(f"  Reading record list from {patient_dir}...")

    # Try to read the master header directly
    # The waveform record name can be found from our segment names
    return patient_dir


def analyze_patient(match):
    """Find which waveform segment overlaps with the PAC insertion time."""
    subject_id = match["subject_id"]
    insertion_time = datetime.strptime(match["starttime"], "%Y-%m-%d %H:%M:%S")
    segments = match["waveform_segments"]
    record_dir = segments[0]["record_dir"]

    # Extract the base record name from segment names
    # e.g., "3187965_0001" -> master record is "3187965"
    master_name = segments[0]["segment_name"].rsplit("_", 1)[0]

    print(f"\nPatient {subject_id}")
    print(f"  PAC insertion: {match['starttime']}")
    print(f"  Master record: {master_name}")
    print(f"  Record dir:    {record_dir}")

    # Read the master header to get segment base times
    print(f"  Reading master header from PhysioNet...")
    try:
        master = wfdb.rdheader(master_name, pn_dir=record_dir)
    except Exception as e:
        print(f"  ERROR reading master header: {e}")
        return None

    # The master header has base_datetime and segment info
    base_time = master.base_datetime
    if base_time is None:
        # Try base_date + base_time
        base_date = getattr(master, 'base_date', None)
        base_t = getattr(master, 'base_time', None)
        if base_date and base_t:
            base_time = datetime.combine(base_date, base_t)

    if base_time is None:
        print(f"  WARNING: No base datetime in master header")
        print(f"  Available attrs: base_date={getattr(master, 'base_date', 'N/A')}, "
              f"base_time={getattr(master, 'base_time', 'N/A')}")
        return None

    print(f"  Recording start: {base_time}")
    print(f"  Total samples:   {master.sig_len}")
    print(f"  Sampling rate:   {master.fs} Hz")

    total_duration = master.sig_len / master.fs
    recording_end = base_time + timedelta(seconds=total_duration)
    print(f"  Recording end:   {recording_end}")

    # Check if insertion falls within the recording window
    if insertion_time < base_time or insertion_time > recording_end:
        print(f"\n  NOTE: Insertion time ({insertion_time}) is OUTSIDE "
              f"the recording window ({base_time} to {recording_end})")
        diff = insertion_time - base_time
        print(f"  Offset from recording start: {diff}")
    else:
        offset = (insertion_time - base_time).total_seconds()
        print(f"\n  Insertion is {offset:.0f} seconds ({offset/3600:.1f} hours) "
              f"into the recording")

    # Now read individual segment headers to find exact segment timing
    # Multi-segment records have segments listed in order
    # Each segment's start can be computed from the cumulative sample count
    print(f"\n  Segment timing:")

    seg_names = getattr(master, 'seg_name', None)
    seg_lens = getattr(master, 'seg_len', None)

    if seg_names and seg_lens:
        cumulative_samples = 0
        results = []

        for seg_name, seg_len in zip(seg_names, seg_lens):
            if seg_name == "~":  # gap segment
                cumulative_samples += seg_len
                continue

            seg_start_sec = cumulative_samples / master.fs
            seg_end_sec = (cumulative_samples + seg_len) / master.fs
            seg_start_time = base_time + timedelta(seconds=seg_start_sec)
            seg_end_time = base_time + timedelta(seconds=seg_end_sec)

            # Check if this is one of our PAP segments
            is_pap_seg = any(s["segment_name"] == seg_name
                           for s in segments)

            # Check if insertion falls in this segment
            contains_insertion = (seg_start_time <= insertion_time <= seg_end_time)

            marker = ""
            if contains_insertion:
                marker = " <<<< PAC INSERTION HERE"
                insertion_offset = (insertion_time - seg_start_time).total_seconds()
                marker += f" (at {insertion_offset:.0f}s into segment)"

            if is_pap_seg or contains_insertion:
                dur = seg_len / master.fs
                dur_str = f"{dur/3600:.1f}h" if dur > 3600 else f"{dur/60:.0f}m"
                print(f"    {seg_name}: {seg_start_time} to {seg_end_time} "
                      f"({dur_str}){' [HAS PAP]' if is_pap_seg else ''}{marker}")

                if contains_insertion:
                    results.append({
                        "segment_name": seg_name,
                        "record_dir": record_dir,
                        "seg_start_time": str(seg_start_time),
                        "insertion_offset_sec": insertion_offset,
                        "segment_duration_sec": seg_len / master.fs,
                        "has_pap": is_pap_seg,
                    })

            cumulative_samples += seg_len

        return results
    else:
        print("  Could not read segment layout from master header")
        return None


def main():
    print("=" * 70)
    print("PAC Insertion <-> Waveform Alignment")
    print("=" * 70)

    # Load matches
    json_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pac_insertions.json"
    )
    with open(json_path, "r") as f:
        data = json.load(f)

    matches = data["matches"]
    print(f"Found {len(matches)} patients with PAC insertion + waveforms\n")

    all_results = []
    for match in matches:
        result = analyze_patient(match)
        if result:
            all_results.append({
                "subject_id": match["subject_id"],
                "insertion_time": match["starttime"],
                "aligned_segments": result,
            })

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY — Segments to explore in the waveform viewer")
    print(f"{'='*70}")

    for r in all_results:
        for seg in r["aligned_segments"]:
            offset = seg["insertion_offset_sec"]
            print(f"\n  Patient {r['subject_id']}: segment {seg['segment_name']}")
            print(f"    Insertion at offset: {offset:.0f}s "
                  f"({offset/60:.0f} minutes into segment)")
            print(f"    Has PAP signal: {'Yes' if seg['has_pap'] else 'No'}")
            print(f"    → Open in viewer and navigate to ~{offset:.0f}s")


if __name__ == "__main__":
    main()
