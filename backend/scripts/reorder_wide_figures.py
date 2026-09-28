"""Repair saved v3 documents whose page-wide figures trail lower text columns.

Without --backup-dir this is a dry run. Only block placement and order change;
saved text, IDs, PDF positions, and annotation anchors stay intact.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from backend.app.model import DOCUMENT_MODEL_VERSION, Section
from backend.app.normalizer import _wide_figure_anchor


def repair(model: dict) -> list[dict]:
    if model.get("modelVersion") != DOCUMENT_MODEL_VERSION:
        return []
    originals = [block for section in model.get("sections", []) for block in section.get("blocks", [])]
    by_id = {block["id"]: block for block in originals}
    if len(by_id) != len(originals):
        raise ValueError("Duplicate block IDs")
    heights = {int(page["number"]): float(page["height"]) for page in model.get("pages", [])}
    sections = [Section.model_validate(section) for section in model.get("sections", [])]

    def source_box(block: dict) -> tuple[int, tuple[float, float, float, float]] | None:
        page, box = block.get("page"), block.get("bbox")
        if page not in heights or not box:
            return None
        height = heights[page]
        return page, (box["x"], height - box["y"] - box["height"],
                      box["x"] + box["width"], height - box["y"])

    geometry = {block["id"]: source_box(block) for block in originals if block.get("type") == "paragraph"}
    geometry = {key: value for key, value in geometry.items() if value is not None}
    moved: list[dict] = []
    figures = [(section, block) for section in sections for block in section.blocks if block.type == "figure"]
    for source, figure in figures:
        original = by_id[figure.id]
        if figure.beforeHeading or not (position := source_box(original)):
            continue
        page, (left, bottom, right, top) = position
        target = _wide_figure_anchor(sections, geometry, (page, left, bottom, right, top))
        if not target:
            continue
        target_section, anchor = target
        source_index, target_index = sections.index(source), sections.index(target_section)
        if source_index < target_index or (source is target_section and source.blocks.index(figure) < source.blocks.index(anchor)):
            continue
        source.blocks.remove(figure)
        target_section.blocks.insert(target_section.blocks.index(anchor), figure)
        moved.append({"figure": figure.id, "page": page, "before": anchor.id})
    if not moved:
        return []

    ordered = [block.id for section in sections for block in section.blocks]
    if set(ordered) != set(by_id) or len(ordered) != len(originals):
        raise ValueError("Repair changed block identities")
    text_types = {"paragraph", "list", "quote", "footnote", "requirement", "code", "equation"}
    before_text = [block["id"] for block in originals if block.get("type") in text_types]
    after_text = [key for key in ordered if by_id[key].get("type") in text_types]
    if before_text != after_text:
        raise ValueError("Repair changed text or equation order")
    for section, repaired in zip(model["sections"], sections):
        section["blocks"] = [by_id[block.id] for block in repaired.blocks]
    orders = {key: index for index, key in enumerate(ordered)}
    for block in originals:
        block["order"] = orders[block["id"]]
    for collection in ("figures", "tables"):
        for block in model.get(collection, []):
            if block.get("id") in orders:
                block["order"] = orders[block["id"]]
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("userdata/documents"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--id", action="append", default=[], help="Optional document ID prefix")
    args = parser.parse_args()
    if args.backup_dir and args.backup_dir.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("Backup directory must be outside the document data directory")
    for folder in sorted(args.data_dir.iterdir()):
        path = folder / "document.json"
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", folder.name) or not path.is_file():
            continue
        if args.id and not any(folder.name.startswith(prefix) for prefix in args.id):
            continue
        try:
            model = json.loads(path.read_text(encoding="utf-8"))
            moved = repair(model)
            if moved and args.backup_dir:
                backup = args.backup_dir / folder.name / "document.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    raise ValueError("Backup already exists")
                shutil.copy2(path, backup)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            print(json.dumps({"id": folder.name[:8], "status": "updated" if moved and args.backup_dir else "dry-run" if moved else "unchanged", "moved": moved}))
        except Exception as exc:
            print(json.dumps({"id": folder.name[:8], "status": "skipped", "reason": str(exc)}))


if __name__ == "__main__":
    main()
