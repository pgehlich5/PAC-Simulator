#!/usr/bin/env python3
"""
Find PAC (Swan-Ganz) insertion timestamps in MIMIC-III clinical database
and cross-reference with patients who have waveform data.

Searches PROCEDUREEVENTS_MV for PA catheter-related procedures, then
matches against our pap_records.json catalog to find patients where we
have both a clinical insertion timestamp AND waveform recordings.

Usage:
  python find_pac_insertions.py
"""

import csv
import gzip
import json
import os
from datetime import datetime

# --- Configuration -----------------------------------------------------------
MIMIC_DIR = r"C:\Users\pgehl\Documents\ClaudeCodeProjects\PAC Simulator Project\MIMIC-III Clinical Database Files"
CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pap_records.json"
)

# Known MIMIC-III item IDs for PA catheter procedures
# These are from D_ITEMS table — we'll also search by label keywords
PAC_KEYWORDS = [
    "swan", "pa catheter", "pac ", "pulmonary artery catheter",
    "swan-ganz", "swan ganz", "pa line",
]


def load_gz_csv(filepath, max_rows=None):
    """Load a gzipped CSV file and return list of dicts."""
    rows = []
    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            rows.append(row)
            if max_rows and i >= max_rows:
                break
    return rows


def find_pac_item_ids():
    """Search D_ITEMS for PA catheter-related procedure item IDs."""
    items_path = os.path.join(MIMIC_DIR, "D_ITEMS.csv.gz")
    print("Searching D_ITEMS for PA catheter procedures...")

    pac_items = []
    total = 0

    with gzip.open(items_path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            label = row.get("LABEL", "").lower()
            # Check if any PAC keyword matches
            for kw in PAC_KEYWORDS:
                if kw in label:
                    pac_items.append({
                        "itemid": row["ITEMID"],
                        "label": row["LABEL"],
                        "category": row.get("CATEGORY", ""),
                        "dbsource": row.get("DBSOURCE", ""),
                    })
                    break

    print(f"  Scanned {total} items, found {len(pac_items)} PAC-related:")
    for item in pac_items:
        print(f"    [{item['itemid']}] {item['label']} "
              f"({item['category']}, {item['dbsource']})")

    return pac_items


def find_pac_procedures(pac_item_ids):
    """Search PROCEDUREEVENTS_MV for procedures matching PAC item IDs."""
    proc_path = os.path.join(MIMIC_DIR, "PROCEDUREEVENTS_MV.csv.gz")
    print("\nSearching PROCEDUREEVENTS_MV for PAC insertions...")

    item_id_set = set(pac_item_ids)
    procedures = []
    total = 0

    with gzip.open(proc_path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if row.get("ITEMID") in item_id_set:
                procedures.append({
                    "subject_id": row["SUBJECT_ID"],
                    "hadm_id": row["HADM_ID"],
                    "icustay_id": row.get("ICUSTAY_ID", ""),
                    "itemid": row["ITEMID"],
                    "starttime": row.get("STARTTIME", ""),
                    "endtime": row.get("ENDTIME", ""),
                    "value": row.get("VALUE", ""),
                    "valueuom": row.get("VALUEUOM", ""),
                    "ordercategoryname": row.get("ORDERCATEGORYNAME", ""),
                    "statusdescription": row.get("STATUSDESCRIPTION", ""),
                })

    print(f"  Scanned {total} procedure events, found {len(procedures)} PAC procedures")
    return procedures


def load_waveform_catalog():
    """Load our pap_records.json to get patients with waveform data."""
    if not os.path.exists(CATALOG_PATH):
        print(f"\nWARNING: Waveform catalog not found at {CATALOG_PATH}")
        print("  Run find_pap_records.py first to create it.")
        return {}

    with open(CATALOG_PATH, "r") as f:
        catalog = json.load(f)

    # Build lookup: subject_id -> list of segments
    # Patient IDs in catalog are like "p000020" -> subject_id is "20"
    patient_segments = {}
    for seg in catalog.get("segments", []):
        record_dir = seg.get("record_dir", "")
        parts = record_dir.rstrip("/").split("/")
        patient_id = parts[-1] if parts else ""
        # Extract numeric subject_id: "p000020" -> "20"
        if patient_id.startswith("p"):
            subject_id = str(int(patient_id[1:]))  # Remove leading zeros
            if subject_id not in patient_segments:
                patient_segments[subject_id] = []
            patient_segments[subject_id].append(seg)

    print(f"\nWaveform catalog: {len(patient_segments)} unique patients "
          f"with PAP waveform data")
    return patient_segments


def cross_reference(procedures, pac_items, waveform_patients):
    """Find PAC insertions where we also have waveform data."""
    # Build item label lookup
    item_labels = {item["itemid"]: item["label"] for item in pac_items}

    # Separate matched vs unmatched
    matched = []
    unmatched = []

    for proc in procedures:
        sid = proc["subject_id"]
        proc["item_label"] = item_labels.get(proc["itemid"], "?")

        if sid in waveform_patients:
            proc["waveform_segments"] = waveform_patients[sid]
            matched.append(proc)
        else:
            unmatched.append(proc)

    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"  Total PAC procedures found: {len(procedures)}")
    print(f"  With waveform data:         {len(matched)}")
    print(f"  Without waveform data:      {len(unmatched)}")

    if matched:
        print(f"\n{'='*70}")
        print(f"PAC INSERTIONS WITH WAVEFORM DATA")
        print(f"{'='*70}")
        for i, proc in enumerate(matched, 1):
            segments = proc["waveform_segments"]
            print(f"\n  [{i}] Patient {proc['subject_id']} "
                  f"(HADM: {proc['hadm_id']})")
            print(f"      Procedure: {proc['item_label']}")
            print(f"      Start:     {proc['starttime']}")
            print(f"      End:       {proc['endtime']}")
            print(f"      Status:    {proc['statusdescription']}")
            print(f"      Waveform segments: {len(segments)}")
            for seg in segments[:5]:  # Show first 5 segments
                dur = seg.get("duration_sec", 0)
                dur_str = f"{dur/3600:.1f}h" if dur > 3600 else f"{dur/60:.0f}m"
                print(f"        - {seg['segment_name']} ({dur_str}, "
                      f"signals: {', '.join(seg.get('all_signals', []))})")
            if len(segments) > 5:
                print(f"        ... and {len(segments)-5} more segments")

    # Also print summary of all PAC procedures (even without waveforms)
    print(f"\n{'='*70}")
    print(f"ALL PAC PROCEDURES (first 20)")
    print(f"{'='*70}")
    for proc in procedures[:20]:
        has_wf = " [HAS WAVEFORMS]" if proc["subject_id"] in waveform_patients else ""
        print(f"  Patient {proc['subject_id']:>6} | "
              f"{proc['starttime']:>20} | "
              f"{proc['item_label']}{has_wf}")

    return matched


def main():
    print("=" * 70)
    print("PAC Insertion Finder — MIMIC-III Clinical + Waveform Cross-Reference")
    print("=" * 70)

    # Step 1: Find PAC-related item IDs
    pac_items = find_pac_item_ids()
    if not pac_items:
        print("\nNo PAC-related items found in D_ITEMS. Exiting.")
        return

    # Step 2: Find procedures using those item IDs
    pac_item_ids = [item["itemid"] for item in pac_items]
    procedures = find_pac_procedures(pac_item_ids)
    if not procedures:
        print("\nNo PAC procedures found in PROCEDUREEVENTS_MV. Exiting.")
        return

    # Step 3: Load waveform catalog
    waveform_patients = load_waveform_catalog()

    # Step 4: Cross-reference
    matched = cross_reference(procedures, pac_items, waveform_patients)

    # Save results
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pac_insertions.json"
    )
    output = {
        "total_pac_procedures": len(procedures),
        "matched_with_waveforms": len(matched),
        "matches": [
            {
                "subject_id": m["subject_id"],
                "hadm_id": m["hadm_id"],
                "procedure": m["item_label"],
                "starttime": m["starttime"],
                "endtime": m["endtime"],
                "status": m["statusdescription"],
                "num_waveform_segments": len(m["waveform_segments"]),
                "waveform_segments": [
                    {
                        "segment_name": s["segment_name"],
                        "record_dir": s["record_dir"],
                        "duration_sec": s.get("duration_sec", 0),
                        "all_signals": s.get("all_signals", []),
                        "sampling_rate_hz": s.get("sampling_rate_hz", 125),
                        "num_samples": s.get("num_samples", 0),
                    }
                    for s in m["waveform_segments"]
                ],
            }
            for m in matched
        ],
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_path}")
    print("Done!")


if __name__ == "__main__":
    main()
