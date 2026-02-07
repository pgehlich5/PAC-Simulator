"""
Phase 2: Extract PAP waveform data from MIMIC-III segments identified
by find_pap_records.py.

Reads the pap_records.json file produced by Phase 1, downloads the actual
waveform data for each segment, and saves:
  - Individual CSV files with timestamps and PAP pressure values
  - Summary plots of each waveform
  - A combined metadata CSV for easy browsing

Usage:
    python extract_pap_waveforms.py                  # extract all
    python extract_pap_waveforms.py --limit 5        # extract first 5
    python extract_pap_waveforms.py --plot            # also generate plots
    python extract_pap_waveforms.py --window 300      # first 5 min only
"""

import json
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import wfdb
from tqdm import tqdm

INPUT_FILE = "pap_records.json"
OUTPUT_DIR = "pap_waveforms"


def extract_segment(segment_info, output_dir, max_samples=None, generate_plot=False):
    """
    Download and save PAP waveform data for a single segment.

    Args:
        segment_info: Dict from pap_records.json describing the segment.
        output_dir: Directory to write output files.
        max_samples: If set, only read this many samples from the start.
        generate_plot: If True, save a plot of the waveform.

    Returns:
        Dict with extraction metadata, or None on failure.
    """
    record_dir = segment_info["record_dir"]
    seg_name = segment_info["segment_name"]
    pap_channels = segment_info["pap_channels"]
    fs = segment_info["sampling_rate_hz"]

    # Determine which channels to read
    channel_indices = [ch["channel_index"] for ch in pap_channels]

    try:
        read_kwargs = {
            "record_name": seg_name,
            "pn_dir": record_dir,
            "channels": channel_indices,
        }
        if max_samples:
            read_kwargs["sampfrom"] = 0
            read_kwargs["sampto"] = min(max_samples, segment_info["num_samples"])

        record = wfdb.rdrecord(**read_kwargs)
    except Exception as e:
        return {"segment": seg_name, "error": str(e)}

    # Build a DataFrame with time and PAP values
    n_samples = record.p_signal.shape[0]
    time_seconds = np.arange(n_samples) / fs

    df = pd.DataFrame({"time_sec": time_seconds})
    for i, ch in enumerate(pap_channels):
        col_name = ch["label"]
        df[col_name] = record.p_signal[:, i]
        df[f"{col_name}_units"] = ch["units"]

    # Generate a safe filename from segment name
    safe_name = seg_name.replace("/", "_").replace("\\", "_")
    csv_path = output_dir / f"{safe_name}.csv"
    df.to_csv(csv_path, index=False)

    result = {
        "segment_name": seg_name,
        "record_dir": record_dir,
        "csv_file": str(csv_path),
        "num_samples": n_samples,
        "duration_sec": round(n_samples / fs, 1),
        "sampling_rate_hz": fs,
        "pap_mean": round(float(np.nanmean(record.p_signal[:, 0])), 2),
        "pap_min": round(float(np.nanmin(record.p_signal[:, 0])), 2),
        "pap_max": round(float(np.nanmax(record.p_signal[:, 0])), 2),
    }

    # Optional: generate a plot
    if generate_plot:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(14, 4))

            # Plot at most 30 seconds for readability
            plot_samples = min(n_samples, int(30 * fs))
            t = time_seconds[:plot_samples]
            pap = record.p_signal[:plot_samples, 0]

            ax.plot(t, pap, linewidth=0.5, color="red")
            ax.set_xlabel("Time (seconds)")
            ax.set_ylabel(f"PAP ({pap_channels[0]['units']})")
            ax.set_title(f"PAP Waveform — {seg_name}")
            ax.set_xlim(t[0], t[-1])
            ax.grid(True, alpha=0.3)

            plot_path = output_dir / f"{safe_name}.png"
            fig.savefig(plot_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            result["plot_file"] = str(plot_path)
        except Exception:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Extract PAP waveform data from MIMIC-III"
    )
    parser.add_argument(
        "--input",
        type=str,
        default=INPUT_FILE,
        help=f"Input JSON from Phase 1 (default: {INPUT_FILE})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max segments to extract (0 = all)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=0,
        help="Max seconds of data per segment (0 = full segment)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate PNG plots of each waveform",
    )
    args = parser.parse_args()

    # Load Phase 1 results
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found.")
        print("Run find_pap_records.py first to identify PAP segments.")
        return

    with open(input_path) as f:
        scan_data = json.load(f)

    segments = scan_data["segments"]
    print(f"Loaded {len(segments)} PAP segments from {input_path}")

    if args.limit > 0:
        segments = segments[: args.limit]
        print(f"  Limiting extraction to {args.limit} segments")

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Calculate max_samples from window
    max_samples = None
    if args.window > 0:
        # Assume 125 Hz (standard for MIMIC-III waveforms)
        max_samples = args.window * 125
        print(f"  Window: first {args.window} seconds per segment")

    # Extract each segment
    results = []
    success = 0
    errors = 0

    print(f"\nExtracting PAP waveforms to {output_dir.resolve()}/")
    if args.plot:
        print("  Plot generation: ON")
    print()

    start_time = time.time()

    for seg in tqdm(segments, desc="Extracting"):
        result = extract_segment(seg, output_dir, max_samples, args.plot)
        if result and "error" not in result:
            results.append(result)
            success += 1
        else:
            errors += 1
            if result:
                results.append(result)

    elapsed = time.time() - start_time

    # Save metadata summary
    if results:
        meta_df = pd.DataFrame(results)
        meta_path = output_dir / "extraction_summary.csv"
        meta_df.to_csv(meta_path, index=False)

    # Print summary
    print(f"\n{'='*60}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Segments processed: {len(segments)}")
    print(f"  Successful:         {success}")
    print(f"  Errors:             {errors}")
    print(f"  Elapsed time:       {elapsed:.1f}s")
    print(f"  Output directory:   {output_dir.resolve()}")

    if success > 0:
        successful = [r for r in results if "error" not in r]
        total_samples = sum(r["num_samples"] for r in successful)
        total_duration = sum(r["duration_sec"] for r in successful)
        print(f"\n  Total PAP samples:  {total_samples:,}")
        print(f"  Total duration:     {total_duration/60:.1f} min ({total_duration/3600:.1f} hrs)")
        print(f"  Mean PAP range:     {min(r['pap_min'] for r in successful):.0f} - "
              f"{max(r['pap_max'] for r in successful):.0f} mmHg")
        print(f"\n  Summary CSV:        {meta_path.resolve()}")


if __name__ == "__main__":
    main()
