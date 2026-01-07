# Convert-gcode.3mf-multi-2-single_plate
Script to convert a multi-plate .gcode.3mf file to a single plate gcode.3mf file.

# convert_3mf_to_single_plate.py

Convert an **Orca Slicer / Bambu Studio** multi-plate `*.gcode.3mf` project bundle into a **single-plate** project bundle that opens cleanly in Orca/Bambu.

This script is designed specifically for **`.gcode.3mf` project archives** produced by Orca Slicer and Bambu Studio.

## What it does

- Detects the **exported plate** by parsing `Metadata/model_settings.config` (XML)
  - Selects the plate whose `gcode_file` metadata is **non-empty** and **exists in the archive**
- Converts the project to **Option B** behavior:
  - Keeps only that plate’s assets
  - Renumbers the kept plate to **Plate 1** internally (so the output has `plate_1.*`)
- Preserves project usability in **Orca/Bambu**
  - Keeps and updates project metadata so the output still opens as a project
- Fixes common manual-zip mistakes:
  - If the `.3mf` ZIP contains a single wrapper folder at the root, it **flattens** it
  - Removes macOS junk such as `__MACOSX/` and `.DS_Store`
- Best-effort behavior:
  - If something can’t be rewritten, the script **warns** and proceeds when possible

## Output naming

The script appends the **original detected plate number** to the output filename:

- `foo.gcode.3mf` → `foo_plate2.gcode.3mf`

If that output name already exists, it writes:

- `foo_plate2_1.gcode.3mf`
- `foo_plate2_2.gcode.3mf`
- etc.

## Fast path (already single-plate)

If the input is already a clean single-plate project (Plate 1 only), the script performs a fast path and **copies the file through** (still producing a new output file name based on the plate suffix rules above).

## Requirements

- Python 3.9+ (3.8+ likely works)
- No third-party dependencies

## Usage

```bash
python convert_3mf_to_single_plate.py "/path/to/input.gcode.3mf" -o "/path/to/output_dir"
```




# batch_convert_3mf_to_single_plate.py

Batch-run `convert_3mf_to_single_plate.py` across a directory of Orca/Bambu `*.gcode.3mf` files while **mirroring the input folder structure** under an output directory.

This script is intentionally simple:
- It does not parse or modify 3MF contents itself
- It calls the converter once per file
- Failures do not stop the entire batch

## What it does

- Scans an input directory for `*.gcode.3mf`
- (Optional) scans recursively with `--recursive`
- For each file:
  - Computes its relative folder path under the input directory
  - Creates that same folder path under the output directory
  - Runs `convert_3mf_to_single_plate.py` with `-o` pointing at the mirrored folder

▶️ Batch Conversion Examples
Convert all .gcode.3mf in one directory
```python batch_convert_3mf_to_single_plate.py \
  ./projects \
  -o ./single_plate
```

Recursive (recommended for real project trees)
```python batch_convert_3mf_to_single_plate.py \
  ./projects \
  -o ./single_plate \
  --recursive
```

Dry-run (safe preview)
```python batch_convert_3mf_to_single_plate.py \
  ./projects \
  -o ./single_plate \
  --recursive \
  --dry-run
```
