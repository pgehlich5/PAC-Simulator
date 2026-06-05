"""
Extract waveform data for patient p007452 ("Esther") from MIMIC-III segment
3217478_0011 -- a complete PAC float found by the wedge-anchored scan.

Chamber time ranges (from PAP channel), provided by physician review:
  SVC/RA: 219-255s  (same clip used for both SVC and RA)
  RV:     256-263s
  PA:     263-302s
  Wedge:  305-330s

Background ECG (II) + ABP: 180-205s (clean pre-float window, used for all
chambers). No background_rv -- the RV passage shows no ectopy and is only ~7s.

Modeled on extract_p003914.py.
"""

import os
import json
import numpy as np
import wfdb

SEGMENT = "3217478_0011"
RECORD_DIR = "mimic3wdb-matched/1.0/p00/p007452"
FS = 125

OUTPUT_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "waveform_data", "p007452",
)

BG_START, BG_END = 180, 205          # clean background window (II + ABP)
FLOAT_START, FLOAT_END = 219, 330    # span covering all chamber clips

CHAMBERS = {
    "pap_svc":   {"start": 219, "end": 255,
                  "desc": "PAP waveform for SVC/RA chamber position (early) - Patient p007452"},
    "pap_ra":    {"start": 219, "end": 255,
                  "desc": "PAP waveform for Right Atrium chamber position - Patient p007452"},
    "pap_rv":    {"start": 256, "end": 263,
                  "desc": "PAP waveform for Right Ventricle chamber position - Patient p007452"},
    "pap_pa":    {"start": 263, "end": 302,
                  "desc": "PAP waveform for Pulmonary Artery chamber position - Patient p007452"},
    "pap_wedge": {"start": 305, "end": 328.6,
                  "desc": "PAP waveform for Pulmonary Capillary Wedge position - Patient p007452 "
                          "(trimmed at 328.6s to drop a stray RV beat at the clip end)"},
}


def fetch_window(start_sec, end_sec):
    print(f"  Fetching {start_sec}-{end_sec}s...")
    return wfdb.rdrecord(SEGMENT, pn_dir=RECORD_DIR,
                         sampfrom=int(start_sec * FS), sampto=int(end_sec * FS))


def sig_idx(record, name):
    return record.sig_name.index(name)


def save_signal_csv(folder, signal_name, data, units):
    path = os.path.join(folder, f"{signal_name}.csv")
    with open(path, "w") as f:
        f.write(f"{signal_name} ({units})\n")
        for val in data:
            f.write("0.0\n" if np.isnan(val) else f"{val:.4f}\n")
    print(f"    Saved {path} ({len(data)} samples)")


def save_metadata(folder, case_name, description, start_sec, duration_sec, signals_info):
    meta = {
        "case_name": case_name,
        "description": description,
        "source": {
            "database": "mimic3wdb-matched/1.0",
            "patient_id": "p007452",
            "segment_name": SEGMENT,
            "record_dir": RECORD_DIR,
            "start_seconds": start_sec,
            "duration_seconds": duration_sec,
        },
        "sampling_rate_hz": FS,
        "num_samples": signals_info[0]["num_samples"],
        "signals": signals_info,
    }
    with open(os.path.join(folder, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"    Saved metadata.json")


def main():
    print(f"Extracting p007452 (Esther) from {SEGMENT}\nOutput: {OUTPUT_BASE}\n")

    # --- Background (II + ABP) ---
    print(f"=== Background ({BG_START}-{BG_END}s) ===")
    bg = fetch_window(BG_START, BG_END)
    bg_ii = bg.p_signal[:, sig_idx(bg, "II")]
    bg_abp = bg.p_signal[:, sig_idx(bg, "ABP")]
    bg_folder = os.path.join(OUTPUT_BASE, "background")
    os.makedirs(bg_folder, exist_ok=True)
    save_signal_csv(bg_folder, "II", bg_ii, "mV")
    save_signal_csv(bg_folder, "ABP", bg_abp, "mmHg")
    save_metadata(bg_folder, "background",
                  "Background signals (ECG II, ABP) for continuous playback - Patient p007452",
                  BG_START, BG_END - BG_START,
                  [{"signal_name": "II", "original_label": "II", "units": "mV",
                    "num_samples": len(bg_ii), "file": "II.csv"},
                   {"signal_name": "ABP", "original_label": "ABP", "units": "mmHg",
                    "num_samples": len(bg_abp), "file": "ABP.csv"}])
    print()

    # --- One fetch covering all chamber PAP clips ---
    print(f"=== Float region ({FLOAT_START}-{FLOAT_END}s) ===")
    fr = fetch_window(FLOAT_START, FLOAT_END)
    pap = fr.p_signal[:, sig_idx(fr, "PAP")]

    for name, cfg in CHAMBERS.items():
        print(f"=== {name} ({cfg['start']}-{cfg['end']}s) ===")
        folder = os.path.join(OUTPUT_BASE, name)
        os.makedirs(folder, exist_ok=True)
        lo = int((cfg["start"] - FLOAT_START) * FS)
        hi = int((cfg["end"] - FLOAT_START) * FS)
        clip = pap[lo:hi]
        save_signal_csv(folder, "PAP", clip, "mmHg")
        save_metadata(folder, name, cfg["desc"], cfg["start"], cfg["end"] - cfg["start"],
                      [{"signal_name": "PAP", "original_label": "PAP", "units": "mmHg",
                        "num_samples": len(clip), "file": "PAP.csv"}])

    # --- patient.json ---
    patient_json = {
        "nickname": "Esther",
        "subject_id": 7452,
        "mimic_id": "p007452",
        "age": None,
        "sex": None,
        "summary": "Complete PAC float captured on the PAP channel: catheter "
                   "advanced SVC/RA -> RV -> PA -> wedge. Found by the "
                   "wedge-anchored scan. Clinical details TBD (pull from MIMIC-III).",
        "notes": "First teaching case sourced by the automated wedge-anchored "
                 "float finder. No background_rv (no RV ectopy in this float)."
    }
    with open(os.path.join(OUTPUT_BASE, "patient.json"), "w") as f:
        json.dump(patient_json, f, indent=4)
    print(f"\nSaved patient.json")
    print(f"\n=== DONE === {OUTPUT_BASE}")


if __name__ == "__main__":
    main()
