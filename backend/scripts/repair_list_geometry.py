"""Expand saved v3 list boxes when every source item is uniquely located.

Without --backup-dir this is a dry run. Ambiguous, cross-page, and cross-column
lists are left untouched. The document's text, IDs, and reading order do not change.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from backend.app.model import DOCUMENT_MODEL_VERSION


def _clean(value: str) -> str:
    return " ".join(value.split())


def _source_box(provenance: dict, height: float) -> dict | None:
    box = provenance.get("bbox")
    if not box:
        return None
    left, right = sorted((float(box["l"]), float(box["r"])))
    bottom, top = sorted((float(box["b"]), float(box["t"])))
    if str(box.get("coord_origin", "")).upper() == "BOTTOMLEFT":
        y = height - top
    elif str(box.get("coord_origin", "")).upper() == "TOPLEFT":
        y = bottom
    else:
        return None
    return {"x": left, "y": y, "width": right - left, "height": top - bottom}


def repair(model: dict, snapshot: dict) -> list[dict]:
    if model.get("modelVersion") != DOCUMENT_MODEL_VERSION:
        return []
    heights = {int(page["number"]): float(page["height"]) for page in model.get("pages", [])}
    raw_items = [item for item in snapshot.get("texts", []) if item.get("label") == "list_item"
                 and item.get("prov") and len(_clean(item.get("text", ""))) > 15]
    changed: list[dict] = []
    for section in model.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") != "list" or len(block.get("items", [])) < 2:
                continue
            page = block.get("page")
            if page not in heights or not block.get("bbox"):
                continue
            boxes = []
            for value in block["items"]:
                target = _clean(value)
                matches = [item for item in raw_items if target.endswith(_clean(item.get("text", "")))]
                if len(matches) != 1 or int(matches[0]["prov"][0]["page_no"]) != page:
                    break
                box = _source_box(matches[0]["prov"][0], heights[page])
                if not box:
                    break
                boxes.append(box)
            if len(boxes) != len(block["items"]):
                continue
            first, saved = boxes[0], block["bbox"]
            if any(abs(first[key] - saved[key]) > 3 for key in ("x", "y", "width", "height")):
                continue
            if any(max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
                   < 0.6 * min(a["width"], b["width"]) for a, b in zip(boxes, boxes[1:])):
                continue
            left = min(box["x"] for box in boxes)
            top = min(box["y"] for box in boxes)
            right = max(box["x"] + box["width"] for box in boxes)
            bottom = max(box["y"] + box["height"] for box in boxes)
            union = {"x": left, "y": top, "width": right - left, "height": bottom - top}
            if all(abs(union[key] - saved[key]) < 0.1 for key in union):
                continue
            block["bbox"] = union
            changed.append({"block": block["id"], "page": page, "items": len(boxes)})
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("userdata/documents"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--id", action="append", default=[], help="Optional document ID prefix")
    args = parser.parse_args()
    if args.backup_dir and args.backup_dir.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("Backup directory must be outside the document data directory")
    for folder in sorted(args.data_dir.iterdir()):
        path, snapshot_path = folder / "document.json", folder / "docling.json"
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", folder.name) or not path.is_file() or not snapshot_path.is_file():
            continue
        if args.id and not any(folder.name.startswith(prefix) for prefix in args.id):
            continue
        try:
            model = json.loads(path.read_text(encoding="utf-8"))
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            changed = repair(model, snapshot)
            if changed and args.backup_dir:
                backup = args.backup_dir / folder.name / "document.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    raise ValueError("Backup already exists")
                shutil.copy2(path, backup)
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            print(json.dumps({"id": folder.name[:8], "status": "updated" if changed and args.backup_dir else "dry-run" if changed else "unchanged", "changed": changed}))
        except Exception as exc:
            print(json.dumps({"id": folder.name[:8], "status": "skipped", "reason": str(exc)}))


if __name__ == "__main__":
    main()
