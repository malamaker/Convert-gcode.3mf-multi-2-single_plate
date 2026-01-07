#!/usr/bin/env python3
"""
batch_convert_3mf_to_single_plate.py

Batch-run convert_3mf_to_single_plate.py against a directory and mirror
the input folder structure under the output directory.

Example:
  input_dir/A/foo.gcode.3mf   -> output_dir/A/foo_plate2.gcode.3mf
  input_dir/B/C/bar.gcode.3mf -> output_dir/B/C/bar_plate1.gcode.3mf
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batch convert Orca/Bambu .gcode.3mf files to single-plate (plate 1), mirroring folder structure."
    )

    ap.add_argument("input_dir", type=Path, help="Directory containing .gcode.3mf files")
    ap.add_argument("-o", "--output-dir", type=Path, required=True, help="Base output directory")
    ap.add_argument(
        "--script",
        type=Path,
        default=Path("convert_3mf_to_single_plate.py"),
        help="Path to convert_3mf_to_single_plate.py",
    )
    ap.add_argument("--recursive", action="store_true", help="Recursively scan input_dir")
    ap.add_argument("--dry-run", action="store_true", help="Print commands without executing")

    args = ap.parse_args()

    if not args.input_dir.is_dir():
        print(f"ERROR: Input directory not found: {args.input_dir}", file=sys.stderr)
        sys.exit(2)

    if not args.script.exists():
        print(f"ERROR: Converter script not found: {args.script}", file=sys.stderr)
        sys.exit(2)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    iterator = (
        args.input_dir.rglob("*.gcode.3mf")
        if args.recursive
        else args.input_dir.glob("*.gcode.3mf")
    )

    files = sorted(p for p in iterator if p.is_file())

    if not files:
        print("No .gcode.3mf files found.")
        return

    print(f"Found {len(files)} file(s) to process.\n")

    ok = 0
    failed = 0

    for src in files:
        rel_parent = src.parent.relative_to(args.input_dir)  # e.g. B/C
        dst_dir = args.output_dir / rel_parent               # e.g. output_dir/B/C
        dst_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            str(args.script),
            str(src),
            "-o",
            str(dst_dir),
        ]

        if args.dry_run:
            print("[DRY-RUN]", " ".join(cmd))
            continue

        print(f"Processing: {src.relative_to(args.input_dir)}")
        try:
            subprocess.run(cmd, check=True)
            ok += 1
        except subprocess.CalledProcessError:
            print(f"ERROR: Failed to process {src}", file=sys.stderr)
            failed += 1

    print("\nBatch complete.")
    print(f"  Success: {ok}")
    print(f"  Failed : {failed}")


if __name__ == "__main__":
    main()
