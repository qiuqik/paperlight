"""Upgrade saved pre-versioned DocumentModels using their existing Docling snapshots.

Run a dry run first. An upgrade keeps the document ID and rejects changes to
block identities or existing annotation anchors. Original JSON and replaced
assets are copied to --backup-dir before a document is changed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from docling_core.types.doc import DoclingDocument

from backend.app.model import DOCUMENT_MODEL_VERSION
from backend.app.normalizer import extract_pdf_references, normalize_docling, parse_grobid


def _blocks(model: dict) -> dict[str, dict]:
    blocks = [block for section in model.get("sections", []) for block in section.get("blocks", [])]
    result = {block["id"]: block for block in blocks}
    if len(result) != len(blocks):
        raise ValueError("Duplicate block IDs")
    return result


def _annotation_blocks(annotation: dict) -> set[str]:
    anchor = annotation.get("anchor")
    if isinstance(anchor, dict):
        return {value["blockId"] for value in (anchor.get("start"), anchor.get("end"))
                if isinstance(value, dict) and isinstance(value.get("blockId"), str)} | (
                    {anchor["blockId"]} if isinstance(anchor.get("blockId"), str) else set())
    return {annotation["blockId"]} if isinstance(annotation.get("blockId"), str) else set()


def _check_compatibility(old: dict, new: dict, annotations: list[dict]) -> None:
    before, after = _blocks(old), _blocks(new)
    missing = before.keys() - after.keys()
    if missing:
        raise ValueError(f"{len(missing)} old block IDs disappeared ({len(before)} old, {len(after)} new)")
    if any(before[key].get("type") != after[key].get("type") for key in before):
        raise ValueError("Block types changed")
    if any(before[key].get("text", "") != after[key].get("text", "") for key in before):
        raise ValueError("Existing block text changed")
    annotated = set().union(*(_annotation_blocks(item) for item in annotations)) if annotations else set()
    if not annotated.issubset(after):
        raise ValueError("An annotation references a missing block")
    if any(before[key].get("text", "") != after[key].get("text", "") for key in annotated):
        raise ValueError("Annotated block text changed")
    old_order = {key: index for index, key in enumerate(
        block["id"] for section in old.get("sections", []) for block in section.get("blocks", []))}
    new_order = {key: index for index, key in enumerate(
        block["id"] for section in new.get("sections", []) for block in section.get("blocks", []))}
    old_text_order = [key for key in old_order if before[key].get("type") in
                      {"paragraph", "list", "quote", "footnote", "requirement", "code", "equation"}]
    new_text_order = sorted(old_text_order, key=new_order.__getitem__)
    if old_text_order != new_text_order:
        raise ValueError("Existing text block order changed")
    for item in annotations:
        anchor = item.get("anchor")
        if not isinstance(anchor, dict):
            continue
        start, end = anchor.get("start"), anchor.get("end")
        if isinstance(start, dict) and isinstance(end, dict):
            first, last = start.get("blockId"), end.get("blockId")
            if first in old_order and last in old_order:
                if (old_order[first] < old_order[last]) != (new_order[first] < new_order[last]):
                    raise ValueError("Cross-block annotation order changed")


def upgrade(folder: Path, backup_root: Path | None) -> dict:
    path = folder / "document.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    if old.get("modelVersion") == DOCUMENT_MODEL_VERSION:
        return {"id": folder.name[:8], "status": "current"}
    if not (folder / "original.pdf").is_file() or not (folder / "docling.json").is_file():
        raise ValueError("Missing original PDF or Docling snapshot")
    document = DoclingDocument.model_validate_json((folder / "docling.json").read_text(encoding="utf-8"))
    grobid_path = folder / "grobid.xml"
    grobid = parse_grobid(grobid_path.read_text(encoding="utf-8")) if grobid_path.is_file() else {}
    annotations_path = folder / "annotations.json"
    annotations = json.loads(annotations_path.read_text(encoding="utf-8")) if annotations_path.is_file() else []
    if not isinstance(annotations, list):
        raise ValueError("Invalid annotation file")
    with tempfile.TemporaryDirectory(prefix="paperlight-upgrade-") as temporary:
        stage = Path(temporary)
        shutil.copy2(folder / "original.pdf", stage / "original.pdf")
        model = normalize_docling(document, folder.name, stage, grobid,
                                  extract_pdf_references(stage / "original.pdf"))
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        model.fingerprint = old.get("fingerprint") or status.get("fingerprint", "")
        new = model.model_dump(mode="json")
        _check_compatibility(old, new, annotations)
        blocks = _blocks(new)
        report = {"id": folder.name[:8], "status": "ready-to-upgrade", "blocks": len(blocks),
                  "missingBbox": sum(not block.get("bbox") for block in blocks.values()),
                  "annotations": len(annotations)}
        if backup_root is None:
            return report
        backup = backup_root / folder.name
        if backup.exists():
            raise ValueError("Backup already exists; refusing to overwrite it")
        backup.mkdir(parents=True)
        shutil.copy2(path, backup / "document.json")
        staged_assets = stage / "assets"
        if staged_assets.is_dir():
            for asset in staged_assets.iterdir():
                destination = folder / "assets" / asset.name
                if destination.exists():
                    (backup / "assets").mkdir(exist_ok=True)
                    shutil.copy2(destination, backup / "assets" / asset.name)
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(asset, destination)
        temporary_json = folder / "document.upgrade.tmp"
        temporary_json.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        temporary_json.replace(path)
        report["status"] = "upgraded"
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, help="Apply upgrades and save original files here")
    args = parser.parse_args()
    if args.backup_dir and args.backup_dir.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("Backup directory must be outside the document data directory")
    for folder in sorted(args.data_dir.iterdir()):
        if not folder.is_dir() or len(folder.name) != 32 or not (folder / "document.json").is_file():
            continue
        try:
            print(json.dumps(upgrade(folder, args.backup_dir)))
        except Exception as exc:
            print(json.dumps({"id": folder.name[:8], "status": "skipped", "reason": str(exc)}))


if __name__ == "__main__":
    main()
