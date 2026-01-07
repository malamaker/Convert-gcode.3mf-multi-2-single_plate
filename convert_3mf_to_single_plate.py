#!/usr/bin/env python3
"""
convert_3mf_to_single_plate.py

Convert an Orca / Bambu Studio multi-plate *.gcode.3mf "project bundle"
into a SINGLE-PLATE project bundle, always renumbered to Plate 1.

Key behavior (matches your requirements):
- Input: .gcode.3mf from Orca/Bambu Studio
- Auto-detect exported plate by parsing Metadata/model_settings.config (XML):
    choose plate where gcode_file metadata is non-empty AND that file exists in the archive
- Output: Always plate_1.* internally (Option B)
- Output filename suffix uses ORIGINAL detected plate number:
    foo.gcode.3mf -> foo_plate2.gcode.3mf
    if exists -> foo_plate2_1.gcode.3mf, etc.
- Best effort; warn on issues, don’t crash unless conversion is impossible.
- Fixes "wrapper folder" ZIP mistake (flattens single top-level directory if present)
- Removes macOS junk: __MACOSX/, .DS_Store

Usage:
  python convert_3mf_to_single_plate.py input.gcode.3mf -o /path/to/outdir

Notes:
- This script focuses on the common Orca/Bambu gcode.3mf layout:
  - Metadata/model_settings.config (XML listing plates)
  - Metadata/_rels/model_settings.config.rels (points to gcode)
  - _rels/.rels (cover thumbnails)
  - per-plate assets under Metadata: plate_N.png, plate_N_small.png, plate_no_light_N.png, top_N.png, pick_N.png, plate_N.json, plate_N.gcode, etc.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import xml.etree.ElementTree as ET


MAC_JUNK_PREFIXES = ("__MACOSX/",)
MAC_JUNK_FILES = (".DS_Store",)


# Per-plate filename patterns we will rename/drop.
# Example: Metadata/plate_2.png -> Metadata/plate_1.png
PLATE_FILE_RX = re.compile(r"^(Metadata/)(.+?)_(\d+)(\.[^/]+)$")


@dataclass
class PlateInfo:
    plater_id: int
    gcode_file: str  # as stored in XML (usually "Metadata/plate_2.gcode" or "")
    xml_plate_elem: ET.Element  # <plate> element


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(msg, file=sys.stdout)


def is_mac_junk(name: str) -> bool:
    if any(name.startswith(p) for p in MAC_JUNK_PREFIXES):
        return True
    base = name.rsplit("/", 1)[-1]
    if base in MAC_JUNK_FILES:
        return True
    return False


def flatten_wrapper_prefix(names: List[str]) -> Tuple[List[str], str]:
    """
    If the ZIP has a single top-level folder wrapping everything (common manual-zip mistake),
    return (new_names, prefix_to_strip). Otherwise return (names, "").

    We detect by looking for [Content_Types].xml: it must be at zip root in a valid 3MF.
    If not at root, but exists as "<prefix>/[Content_Types].xml" and all non-junk files share
    that same prefix, we strip it.
    """
    nonjunk = [n for n in names if not is_mac_junk(n) and not n.endswith("/")]
    if "[Content_Types].xml" in nonjunk:
        return names, ""  # already correct

    # Find any occurrence of */[Content_Types].xml
    candidates = [n for n in nonjunk if n.endswith("/[Content_Types].xml")]
    if not candidates:
        return names, ""

    # Choose the shortest prefix candidate
    candidates.sort(key=len)
    candidate = candidates[0]
    prefix = candidate[: -len("[Content_Types].xml")]
    # Ensure every nonjunk file starts with that prefix
    if all(n.startswith(prefix) for n in nonjunk):
        return names, prefix
    return names, ""


def read_text(z: zipfile.ZipFile, name: str) -> Optional[str]:
    try:
        return z.read(name).decode("utf-8")
    except KeyError:
        return None
    except UnicodeDecodeError:
        warn(f"Could not decode as UTF-8: {name}")
        return None


def parse_model_settings_config(xml_text: str) -> List[PlateInfo]:
    """
    Parse Metadata/model_settings.config and return plates found.
    We look for:
      <plate> ... <metadata key="plater_id" value="2"/> ... <metadata key="gcode_file" value="Metadata/plate_2.gcode"/>
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse model_settings.config XML: {e}") from e

    plates: List[PlateInfo] = []
    for plate_elem in root.findall("./plate"):
        plater_id = None
        gcode_file = ""
        for md in plate_elem.findall("./metadata"):
            k = md.attrib.get("key")
            v = md.attrib.get("value", "")
            if k == "plater_id":
                try:
                    plater_id = int(v)
                except ValueError:
                    pass
            elif k == "gcode_file":
                gcode_file = v or ""
        if plater_id is not None:
            plates.append(PlateInfo(plater_id=plater_id, gcode_file=gcode_file, xml_plate_elem=plate_elem))
    return plates


def detect_exported_plate_id(z: zipfile.ZipFile, names: List[str], prefix: str) -> Tuple[int, str]:
    """
    Detect exported plate by parsing Metadata/model_settings.config (XML) and picking:
      - plate with non-empty gcode_file
      - and that gcode_file exists in ZIP

    Returns (plate_id, gcode_file_path_in_zip).
    """
    ms_name = prefix + "Metadata/model_settings.config"
    ms_text = read_text(z, ms_name)
    if not ms_text:
        raise RuntimeError("Missing or unreadable Metadata/model_settings.config")

    plates = parse_model_settings_config(ms_text)
    if not plates:
        raise RuntimeError("No <plate> entries found in Metadata/model_settings.config")

    name_set = set(names)

    # Prefer plate(s) whose gcode_file exists
    candidates: List[Tuple[int, str]] = []
    for p in plates:
        if p.gcode_file.strip():
            gpath = prefix + p.gcode_file.lstrip("/")
            if gpath in name_set:
                candidates.append((p.plater_id, gpath))

    if candidates:
        # If multiple, choose the lowest plater_id deterministically
        candidates.sort(key=lambda t: t[0])
        return candidates[0][0], candidates[0][1]

    # Fallback: if there is exactly one plate with a non-empty gcode_file (even if missing), choose it
    nonempty = [p for p in plates if p.gcode_file.strip()]
    if len(nonempty) == 1:
        gpath = prefix + nonempty[0].gcode_file.lstrip("/")
        warn(f"gcode_file listed but not found in archive: {nonempty[0].gcode_file}")
        return nonempty[0].plater_id, gpath

    # Final fallback: choose plate 1
    warn("Could not confidently detect exported plate from gcode_file; defaulting to plate 1")
    return 1, prefix + "Metadata/plate_1.gcode"


def compute_output_path(input_path: Path, out_dir: Path, exported_plate_id: int) -> Path:
    """
    Create output name:
      foo.gcode.3mf -> foo_plate2.gcode.3mf
      If exists -> foo_plate2_1.gcode.3mf etc.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    name = input_path.name
    if name.lower().endswith(".gcode.3mf"):
        base = name[:-len(".gcode.3mf")]
        ext = ".gcode.3mf"
    else:
        # Shouldn't happen given your scope, but keep safe
        base = input_path.stem
        ext = input_path.suffix

    candidate = out_dir / f"{base}_plate{exported_plate_id}{ext}"
    if not candidate.exists():
        return candidate

    i = 1
    while True:
        candidate_i = out_dir / f"{base}_plate{exported_plate_id}_{i}{ext}"
        if not candidate_i.exists():
            return candidate_i
        i += 1


def rewrite_xml_cover_rels(xml_text: str, old_plate: int, new_plate: int) -> str:
    """
    Update _rels/.rels cover thumbnails:
      /Metadata/plate_2.png -> /Metadata/plate_1.png
      /Metadata/plate_2_small.png -> /Metadata/plate_1_small.png
    """
    def sub_target(s: str) -> str:
        s = s.replace(f"/Metadata/plate_{old_plate}.png", f"/Metadata/plate_{new_plate}.png")
        s = s.replace(f"/Metadata/plate_{old_plate}_small.png", f"/Metadata/plate_{new_plate}_small.png")
        return s

    return sub_target(xml_text)


def rewrite_model_settings_config(xml_text: str, keep_plate_id: int, new_plate_id: int = 1) -> str:
    """
    Keep only the specified plate entry and renumber it to new_plate_id (always 1).
    Also rewrite common per-plate metadata file references within that plate:
      Metadata/plate_2.png -> Metadata/plate_1.png
      Metadata/plate_no_light_2.png -> Metadata/plate_no_light_1.png
      Metadata/top_2.png -> Metadata/top_1.png
      Metadata/pick_2.png -> Metadata/pick_1.png
      Metadata/plate_2.gcode -> Metadata/plate_1.gcode
      Metadata/plate_2.json -> Metadata/plate_1.json (if referenced)
    """
    root = ET.fromstring(xml_text)

    plates = root.findall("./plate")
    if not plates:
        return xml_text

    keep_elem = None
    for plate_elem in plates:
        pid = None
        for md in plate_elem.findall("./metadata"):
            if md.attrib.get("key") == "plater_id":
                try:
                    pid = int(md.attrib.get("value", ""))
                except ValueError:
                    pid = None
        if pid == keep_plate_id:
            keep_elem = plate_elem
            break

    if keep_elem is None:
        warn(f"Could not find plate {keep_plate_id} in model_settings.config; leaving XML unchanged")
        return xml_text

    # Remove all plate elems, then append the kept one
    for plate_elem in plates:
        root.remove(plate_elem)
    root.append(keep_elem)

    # Rewrite metadata within kept plate
    for md in keep_elem.findall("./metadata"):
        k = md.attrib.get("key")
        v = md.attrib.get("value", "")
        if k == "plater_id":
            md.set("value", str(new_plate_id))
            continue

        # rewrite known per-plate file refs
        v2 = v
        v2 = v2.replace(f"plate_{keep_plate_id}.gcode", f"plate_{new_plate_id}.gcode")
        v2 = v2.replace(f"plate_{keep_plate_id}.png", f"plate_{new_plate_id}.png")
        v2 = v2.replace(f"plate_{keep_plate_id}_small.png", f"plate_{new_plate_id}_small.png")
        v2 = v2.replace(f"plate_no_light_{keep_plate_id}.png", f"plate_no_light_{new_plate_id}.png")
        v2 = v2.replace(f"top_{keep_plate_id}.png", f"top_{new_plate_id}.png")
        v2 = v2.replace(f"pick_{keep_plate_id}.png", f"pick_{new_plate_id}.png")
        v2 = v2.replace(f"front_{keep_plate_id}.png", f"front_{new_plate_id}.png")
        v2 = v2.replace(f"back_{keep_plate_id}.png", f"back_{new_plate_id}.png")
        v2 = v2.replace(f"plate_{keep_plate_id}.json", f"plate_{new_plate_id}.json")
        if v2 != v:
            md.set("value", v2)

    # Serialize back (ElementTree won't preserve formatting; acceptable for best-effort)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def rewrite_slice_info_config(xml_text: str, keep_plate_id: int, new_plate_id: int = 1) -> str:
    """
    Metadata/slice_info.config often contains:
      <plate><metadata key="index" value="2"/>...
    Convert index to 1 and remove other plate nodes if any.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        warn("Failed to parse slice_info.config; leaving unchanged")
        return xml_text

    plate_elems = root.findall("./plate")
    if not plate_elems:
        return xml_text

    # If multiple, try keep one whose index matches keep_plate_id; else keep first.
    keep_elem = None
    for pe in plate_elems:
        idx = None
        for md in pe.findall("./metadata"):
            if md.attrib.get("key") == "index":
                try:
                    idx = int(md.attrib.get("value", ""))
                except ValueError:
                    idx = None
        if idx == keep_plate_id:
            keep_elem = pe
            break
    if keep_elem is None:
        keep_elem = plate_elems[0]

    for pe in plate_elems:
        root.remove(pe)
    root.append(keep_elem)

    for md in keep_elem.findall("./metadata"):
        if md.attrib.get("key") == "index":
            md.set("value", str(new_plate_id))

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def rewrite_3dmodel_thumbnails(xml_text: str, new_plate_id: int = 1) -> str:
    """
    3D/3dmodel.model includes <metadata name="Thumbnail_Middle">/Metadata/plate_1.png</metadata>
    We'll force it to plate_1.*.
    """
    # Conservative text replacements
    xml_text = re.sub(r'(<metadata\s+name="Thumbnail_Middle">)\s*/Metadata/plate_\d+\.png(\s*</metadata>)',
                      rf"\1/Metadata/plate_{new_plate_id}.png\2",
                      xml_text)
    xml_text = re.sub(r'(<metadata\s+name="Thumbnail_Small">)\s*/Metadata/plate_\d+_small\.png(\s*</metadata>)',
                      rf"\1/Metadata/plate_{new_plate_id}_small.png\2",
                      xml_text)
    return xml_text


def rename_or_drop_plate_file(name: str, keep_plate_id: int, new_plate_id: int = 1) -> Tuple[bool, str]:
    """
    Decide whether to keep a Metadata/*_<N>.<ext> file and if so, what its new name should be.
    - Keep only if N == keep_plate_id
    - Rename N -> new_plate_id
    """
    m = PLATE_FILE_RX.match(name)
    if not m:
        return True, name  # not a plate-numbered Metadata asset

    prefix, stem, num_s, ext = m.groups()
    try:
        num = int(num_s)
    except ValueError:
        return True, name

    if num != keep_plate_id:
        return False, name

    # Rename to new_plate_id
    new_name = f"{prefix}{stem}_{new_plate_id}{ext}"
    return True, new_name


def is_already_single_plate(z: zipfile.ZipFile, names: List[str], prefix: str) -> bool:
    """
    Fast-path check:
    - model_settings.config has exactly 1 plate entry
    - _rels/.rels points at plate_1 thumbnails (or has none)
    - and there are no Metadata/*_N.* for N != 1
    """
    ms = read_text(z, prefix + "Metadata/model_settings.config")
    if not ms:
        return False

    try:
        plates = parse_model_settings_config(ms)
    except Exception:
        return False

    if len(plates) != 1:
        return False

    only_id = plates[0].plater_id
    if only_id != 1:
        return False

    # Any plate-numbered assets besides _1?
    for n in names:
        if is_mac_junk(n) or n.endswith("/"):
            continue
        nn = n[len(prefix):] if prefix and n.startswith(prefix) else n
        m = PLATE_FILE_RX.match(nn)
        if m:
            num = int(m.group(3))
            if num != 1:
                return False

    # Check cover rels targets, if present
    rels_text = read_text(z, prefix + "_rels/.rels")
    if rels_text and ("plate_2" in rels_text or "plate_3" in rels_text):
        return False

    return True


def convert(input_path: Path, out_dir: Path) -> Path:
    with zipfile.ZipFile(input_path, "r") as zin:
        names = zin.namelist()
        _, prefix = flatten_wrapper_prefix(names)

        # Rebuild normalized list (keeping original names for reading)
        normalized_names = [n for n in names if not is_mac_junk(n) and not n.endswith("/")]

        # Detect exported plate
        exported_plate_id, exported_gcode_path = detect_exported_plate_id(zin, names, prefix)

        out_path = compute_output_path(input_path, out_dir, exported_plate_id)

        # Fast path: if already a clean single-plate (plate 1 only), just copy bytes
        if is_already_single_plate(zin, names, prefix):
            info(f"Already single-plate (plate 1). Copying through -> {out_path.name}")
            out_path.write_bytes(input_path.read_bytes())
            return out_path

        info(f"Detected exported plate: {exported_plate_id}")
        info(f"Writing single-plate (renumbered to plate 1): {out_path}")

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            written = 0
            dropped = 0

            for orig_name in normalized_names:
                # Read using original name
                data = zin.read(orig_name)

                # Normalize name by stripping wrapper prefix if needed
                name = orig_name
                if prefix and name.startswith(prefix):
                    name = name[len(prefix):]

                # Drop other plates' numbered assets under Metadata
                keep, new_name = rename_or_drop_plate_file(name, keep_plate_id=exported_plate_id, new_plate_id=1)
                if not keep:
                    dropped += 1
                    continue

                # Rewrite specific known files
                if new_name == "_rels/.rels":
                    try:
                        text = data.decode("utf-8")
                        text = rewrite_xml_cover_rels(text, old_plate=exported_plate_id, new_plate=1)
                        data = text.encode("utf-8")
                    except Exception:
                        warn("Failed to rewrite _rels/.rels; leaving as-is")

                elif new_name == "Metadata/_rels/model_settings.config.rels":
                    try:
                        text = data.decode("utf-8")
                        text = text.replace(f"/Metadata/plate_{exported_plate_id}.gcode", "/Metadata/plate_1.gcode")
                        data = text.encode("utf-8")
                    except Exception:
                        warn("Failed to rewrite Metadata/_rels/model_settings.config.rels; leaving as-is")

                elif new_name == "Metadata/model_settings.config":
                    try:
                        text = data.decode("utf-8")
                        text = rewrite_model_settings_config(text, keep_plate_id=exported_plate_id, new_plate_id=1)
                        data = text.encode("utf-8")
                    except Exception as e:
                        warn(f"Failed to rewrite model_settings.config ({e}); leaving as-is")

                elif new_name == "Metadata/slice_info.config":
                    try:
                        text = data.decode("utf-8")
                        text = rewrite_slice_info_config(text, keep_plate_id=exported_plate_id, new_plate_id=1)
                        data = text.encode("utf-8")
                    except Exception:
                        warn("Failed to rewrite slice_info.config; leaving as-is")

                elif new_name == "3D/3dmodel.model":
                    try:
                        text = data.decode("utf-8")
                        text = rewrite_3dmodel_thumbnails(text, new_plate_id=1)
                        data = text.encode("utf-8")
                    except Exception:
                        warn("Failed to rewrite 3D/3dmodel.model thumbnail metadata; leaving as-is")

                # Also rename any gcode.md5 if it exists for the kept plate
                # (Our rename_or_drop_plate_file already renames Metadata/plate_2.gcode.md5 -> plate_1.gcode.md5)

                # Write file
                zi = zipfile.ZipInfo(new_name)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zi, data)
                written += 1

        info(f"Done. Wrote {out_path.name}")
        return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert Orca/Bambu multi-plate .gcode.3mf into a single-plate (plate 1) project.")
    ap.add_argument("input", type=Path, help="Input .gcode.3mf file")
    ap.add_argument("-o", "--output-dir", type=Path, required=True, help="Output directory for converted files")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: Input not found: {args.input}", file=sys.stderr)
        sys.exit(2)

    if args.input.suffix.lower() != ".3mf" and not args.input.name.lower().endswith(".gcode.3mf"):
        warn("Input does not look like a .gcode.3mf; continuing anyway.")

    try:
        out = convert(args.input, args.output_dir)
    except zipfile.BadZipFile:
        print("ERROR: Input is not a valid zip-based .3mf container.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: Conversion failed: {e}", file=sys.stderr)
        sys.exit(2)

    print(str(out))


if __name__ == "__main__":
    main()
