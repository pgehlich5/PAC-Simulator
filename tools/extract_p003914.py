"""
Extract waveform data for patient p003914 from MIMIC-III segment 3375271_0031.

Chamber time ranges (all from PAP channel):
  SVC/RA: 62-119s
  RV:     120-183s  (all 3 channels - catheter causing ectopy!)
  PA:     235-281s
  Wedge:  282-303s

Background ECG+ABP: 0-25s (used for all chambers except RV)
RV gets its own ECG+ABP from 120-183s to capture the ectopy.
"""

import os
import sys
import json
import numpy as np
import wfdb

SEGMENT = "3375271_0031"
RECORD_DIR = "mimic3wdb-matched/1.0/p00/p003914"
FS = 125  # sampling rate

OUTPUT_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "waveform_data", "grover_p003914"
)

# Define what to extract
CHAMBERS = {
    "pap_svc": {"pap_start": 62, "pap_end": 119, "bg_start": 0, "bg_end": 25, "use_rv_bg": False},
    "pap_ra":  {"pap_start": 62, "pap_end": 119, "bg_start": 0, "bg_end": 25, "use_rv_bg": False},
    "pap_rv":  {"pap_start": 120, "pap_end": 183, "bg_start": 120, "bg_end": 183, "use_rv_bg": True},
    "pap_pa":  {"pap_start": 235, "pap_end": 281, "bg_start": 0, "bg_end": 25, "use_rv_bg": False},
    "pap_wedge": {"pap_start": 282, "pap_end": 303, "bg_start": 0, "bg_end": 25, "use_rv_bg": False},
}


def fetch_window(start_sec, end_sec):
    """Download a time window from PhysioNet."""
    sampfrom = int(start_sec * FS)
    sampto = int(end_sec * FS)
    print(f"  Fetching samples {sampfrom}-{sampto} ({start_sec}-{end_sec}s)...")
    record = wfdb.rdrecord(SEGMENT, pn_dir=RECORD_DIR, sampfrom=sampfrom, sampto=sampto)
    return record


def save_signal_csv(folder, signal_name, data, units):
    """Save a single signal as CSV."""
    path = os.path.join(folder, f"{signal_name}.csv")
    with open(path, "w") as f:
        f.write(f"{signal_name} ({units})\n")
        for val in data:
            if np.isnan(val):
                f.write("0.0\n")
            else:
                f.write(f"{val:.4f}\n")
    print(f"    Saved {path} ({len(data)} samples)")


def save_metadata(folder, case_name, description, start_sec, duration_sec, signals_info):
    """Save metadata JSON."""
    path = os.path.join(folder, "metadata.json")
    meta = {
        "case_name": case_name,
        "description": description,
        "source": {
            "database": "mimic3wdb-matched/1.0",
            "patient_id": "p003914",
            "segment_name": SEGMENT,
            "record_dir": RECORD_DIR,
            "start_seconds": start_sec,
            "duration_seconds": duration_sec,
        },
        "sampling_rate_hz": FS,
        "num_samples": signals_info[0]["num_samples"],
        "signals": signals_info,
    }
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"    Saved {path}")


def get_signal_index(record, name):
    """Find index of a signal by name."""
    for i, n in enumerate(record.sig_name):
        if n == name:
            return i
    raise ValueError(f"Signal '{name}' not found in {record.sig_name}")


def main():
    print("Extracting waveform data for patient p003914")
    print(f"Segment: {SEGMENT}")
    print(f"Record dir: {RECORD_DIR}")
    print(f"Output: {OUTPUT_BASE}")
    print()

    # Pre-fetch the two distinct time windows we need
    print("=== Fetching background window (0-25s) ===")
    bg_record = fetch_window(0, 25)
    bg_ii_idx = get_signal_index(bg_record, "II")
    bg_abp_idx = get_signal_index(bg_record, "ABP")
    bg_ii = bg_record.p_signal[:, bg_ii_idx]
    bg_abp = bg_record.p_signal[:, bg_abp_idx]
    print(f"  Signals: {bg_record.sig_name}, {len(bg_ii)} samples each")

    # Save background folder
    bg_folder = os.path.join(OUTPUT_BASE, "background")
    os.makedirs(bg_folder, exist_ok=True)
    save_signal_csv(bg_folder, "II", bg_ii, "mV")
    save_signal_csv(bg_folder, "ABP", bg_abp, "mmHg")
    save_metadata(bg_folder, "background",
                  "Background signals (ECG II, ABP) for continuous playback - Patient p003914",
                  0, 25,
                  [
                      {"signal_name": "II", "original_label": "II", "units": "mV",
                       "num_samples": len(bg_ii), "file": "II.csv"},
                      {"signal_name": "ABP", "original_label": "ABP", "units": "mmHg",
                       "num_samples": len(bg_abp), "file": "ABP.csv"},
                  ])
    print()

    # Fetch each chamber's PAP window
    # We need: 62-119, 120-183, 235-281, 282-303
    # Optimize by fetching larger chunks
    print("=== Fetching SVC/RA + RV window (62-183s) ===")
    svc_rv_record = fetch_window(62, 183)
    svc_rv_pap_idx = get_signal_index(svc_rv_record, "PAP")
    svc_rv_ii_idx = get_signal_index(svc_rv_record, "II")
    svc_rv_abp_idx = get_signal_index(svc_rv_record, "ABP")

    print("\n=== Fetching PA + Wedge window (235-303s) ===")
    pa_wedge_record = fetch_window(235, 303)
    pa_wedge_pap_idx = get_signal_index(pa_wedge_record, "PAP")

    # Now extract and save each chamber
    chamber_descriptions = {
        "pap_svc": "PAP waveform for SVC/RA chamber position (early) - Patient p003914",
        "pap_ra": "PAP waveform for Right Atrium chamber position - Patient p003914",
        "pap_rv": "PAP waveform for Right Ventricle chamber position (with ectopy!) - Patient p003914",
        "pap_pa": "PAP waveform for Pulmonary Artery chamber position - Patient p003914",
        "pap_wedge": "PAP waveform for Pulmonary Capillary Wedge position - Patient p003914",
    }

    for chamber_name, cfg in CHAMBERS.items():
        print(f"\n=== Extracting {chamber_name} ===")
        folder = os.path.join(OUTPUT_BASE, chamber_name)
        os.makedirs(folder, exist_ok=True)

        pap_start = cfg["pap_start"]
        pap_end = cfg["pap_end"]
        duration = pap_end - pap_start

        # Get PAP data from the right record
        if pap_start >= 235:
            # From pa_wedge_record (starts at 235s)
            local_start = int((pap_start - 235) * FS)
            local_end = int((pap_end - 235) * FS)
            pap_data = pa_wedge_record.p_signal[local_start:local_end, pa_wedge_pap_idx]
        else:
            # From svc_rv_record (starts at 62s)
            local_start = int((pap_start - 62) * FS)
            local_end = int((pap_end - 62) * FS)
            pap_data = svc_rv_record.p_signal[local_start:local_end, svc_rv_pap_idx]

        save_signal_csv(folder, "PAP", pap_data, "mmHg")

        signals_info = [
            {"signal_name": "PAP", "original_label": "PAP", "units": "mmHg",
             "num_samples": len(pap_data), "file": "PAP.csv"},
        ]

        # For RV, also save its own II and ABP (ectopy!)
        if cfg["use_rv_bg"]:
            rv_local_start = int((120 - 62) * FS)
            rv_local_end = int((183 - 62) * FS)
            rv_ii = svc_rv_record.p_signal[rv_local_start:rv_local_end, svc_rv_ii_idx]
            rv_abp = svc_rv_record.p_signal[rv_local_start:rv_local_end, svc_rv_abp_idx]
            save_signal_csv(folder, "II", rv_ii, "mV")
            save_signal_csv(folder, "ABP", rv_abp, "mmHg")

            # Save RV-specific background too
            rv_bg_folder = os.path.join(OUTPUT_BASE, "background_rv")
            os.makedirs(rv_bg_folder, exist_ok=True)
            save_signal_csv(rv_bg_folder, "II", rv_ii, "mV")
            save_signal_csv(rv_bg_folder, "ABP", rv_abp, "mmHg")
            save_metadata(rv_bg_folder, "background_rv",
                          "Background signals during RV passage (ECG II with ectopy, ABP) - Patient p003914",
                          120, 63,
                          [
                              {"signal_name": "II", "original_label": "II", "units": "mV",
                               "num_samples": len(rv_ii), "file": "II.csv"},
                              {"signal_name": "ABP", "original_label": "ABP", "units": "mmHg",
                               "num_samples": len(rv_abp), "file": "ABP.csv"},
                          ])

        save_metadata(folder, chamber_name, chamber_descriptions[chamber_name],
                      pap_start, duration, signals_info)

    # Save patient.json
    patient_json = {
        "nickname": None,
        "subject_id": 3914,
        "mimic_id": "p003914",
        "age": 74,
        "sex": "M",
        "summary": "Anterior STEMI -> acute pulmonary edema -> intubated -> LAD stented -> Swan placed in CCU. Severe AS (AVA 0.6). LVEF 45-50%.",
        "notes": "RV passage shows catheter-induced ectopy on ECG. Uses separate background_rv/ for ECG+ABP during RV chamber."
    }
    patient_path = os.path.join(OUTPUT_BASE, "patient.json")
    with open(patient_path, "w") as f:
        json.dump(patient_json, f, indent=4)
    print(f"\nSaved {patient_path}")

    print("\n=== DONE ===")
    print(f"All files saved to {OUTPUT_BASE}")


if __name__ == "__main__":
    main()
