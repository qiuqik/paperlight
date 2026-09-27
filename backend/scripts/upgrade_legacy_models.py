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
from backend.app.normalizer import _block_bbox, clean_text, extract_pdf_references, normalize_docling, parse_grobid


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


def _merge_geometry(old: dict, new: dict, docling_document=None, pdf_document=None) -> dict:
    """Keep legacy content and IDs, copying only unambiguous PDF positions."""
    before, after = _blocks(old), _blocks(new)
    by_signature: dict[tuple, list[dict]] = {}

    def signature(block: dict) -> tuple:
        return (block.get("type"), block.get("text", ""),
                block.get("number"), block.get("caption", ""))

    for block in after.values():
        if block.get("bbox"):
            by_signature.setdefault(signature(block), []).append(block)
    old_occurrences: dict[tuple, int] = {}
    for block in before.values():
        key = (signature(block), block.get("page"))
        old_occurrences[key] = old_occurrences.get(key, 0) + 1
    matched = 0
    for order, block in enumerate(block for section in old.get("sections", []) for block in section.get("blocks", [])):
        block["order"] = order
        if block.get("bbox"):
            continue
        if old_occurrences[(signature(block), block.get("page"))] != 1:
            continue
        candidates = by_signature.get(signature(block), [])
        same_page = [item for item in candidates if item.get("page") == block.get("page")]
        if same_page:
            candidates = same_page
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        block["bbox"] = candidate["bbox"]
        block["page"] = block.get("page") or candidate.get("page")
        matched += 1
    raw_matched = 0
    if docling_document is not None and pdf_document is not None:
        raw_positions: dict[tuple[str, int], list[dict]] = {}
        for item, _depth in docling_document.iterate_items():
            provenance = getattr(item, "prov", None) or []
            if not provenance:
                continue
            location = provenance[0]
            page = getattr(location, "page_no", None)
            text = clean_text(getattr(item, "text", ""))
            box = _block_bbox(location, pdf_document)
            if text and page and box:
                raw_positions.setdefault((text, int(page)), []).append(box)
        for block in before.values():
            if block.get("bbox") or block.get("type") not in {"paragraph", "quote", "footnote", "requirement", "code"}:
                continue
            if old_occurrences[(signature(block), block.get("page"))] != 1:
                continue
            text = clean_text(block.get("text", ""))
            candidates = raw_positions.get((text, block.get("page")), [])
            if len(candidates) == 1:
                block["bbox"] = candidates[0]
                raw_matched += 1
    if not old.get("pages"):
        old["pages"] = new.get("pages", [])
    for collection in ("figures", "tables"):
        for block in old.get(collection, []):
            source = before.get(block.get("id"))
            if source:
                for key in ("page", "bbox", "order"):
                    if key in source:
                        block[key] = source[key]
    return {"matched": matched, "rawMatched": raw_matched, "blocks": len(before),
            "missingBbox": sum(not block.get("bbox") for block in before.values()),
            "figuresMissingBbox": sum(block.get("type") == "figure" and not block.get("bbox")
                                      for block in before.values())}


def _add_anchored_equations(old: dict, new: dict) -> tuple[list[str], int, dict[str, int]]:
    """Insert cropped equations only between uniquely identified old neighbors."""
    existing_ids = set(_blocks(old))
    existing_assets = {block.get("src") for block in _blocks(old).values() if block.get("src")}
    planned: dict[int, list[tuple[int, int, dict]]] = {}
    equation_count = 0
    source_order = 0
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    def key(block: dict) -> tuple:
        return (block.get("type"), block.get("text", ""), block.get("number"),
                block.get("caption", ""), block.get("page"))

    old_positions: dict[tuple, list[tuple[int, int]]] = {}
    for section_index, section in enumerate(old.get("sections", [])):
        for block_index, block in enumerate(section.get("blocks", [])):
            old_positions.setdefault(key(block), []).append((section_index, block_index))
    new_blocks = [block for section in new.get("sections", []) for block in section.get("blocks", [])]
    for index, equation in enumerate(new_blocks):
        if equation.get("type") != "equation":
            continue
        equation_count += 1
        if not equation.get("bbox") or not equation.get("src") or equation["src"] in existing_assets:
            skip("crop")
            continue
        anchors = []
        for direction in (-1, 1):
            anchor = None
            indices = (range(index - 1, -1, -1) if direction == -1 else
                       range(index + 1, len(new_blocks)))
            for neighbor_index in indices:
                neighbor = new_blocks[neighbor_index]
                positions = old_positions.get(key(neighbor), [])
                if len(positions) == 1:
                    anchor = (*positions[0], neighbor.get("page"))
                    break
            anchors.append(anchor)
        before, after = anchors
        if not before or not after:
            skip("incomplete-anchors")
            continue
        if before[0] != after[0]:
            skip("different-sections")
            continue
        if before[1] >= after[1]:
            skip("conflicting-anchors")
            continue
        if abs((before[2] or 0) - (equation.get("page") or 0)) > 1:
            skip("distant-anchor")
            continue
        if abs((after[2] or 0) - (equation.get("page") or 0)) > 1:
            skip("distant-anchor")
            continue
        section_index = after[0]
        insert_at = after[1]
        added = dict(equation)
        while added["id"] in existing_ids:
            added["id"] = f"added-{added['id']}"
        existing_ids.add(added["id"])
        existing_assets.add(added["src"])
        planned.setdefault(section_index, []).append((insert_at, source_order, added))
        source_order += 1

    assets: list[str] = []
    for section_index, insertions in planned.items():
        blocks = old["sections"][section_index]["blocks"]
        for offset, (index, _source_order, block) in enumerate(sorted(insertions)):
            blocks.insert(index + offset, block)
            assets.append(Path(block["src"]).name)
    for order, block in enumerate(block for section in old.get("sections", []) for block in section.get("blocks", [])):
        block["order"] = order
    return assets, equation_count - len(assets), skipped


def upgrade(folder: Path, backup_root: Path | None, geometry_only: bool = False,
            add_equations: bool = False) -> dict:
    path = folder / "document.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    old_blocks = _blocks(old)
    if old.get("modelVersion") == DOCUMENT_MODEL_VERSION and all(block.get("bbox") for block in old_blocks.values()):
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
        if geometry_only:
            import pypdfium2 as pdfium
            pdf_document = pdfium.PdfDocument(str(stage / "original.pdf"))
            try:
                geometry_report = _merge_geometry(old, new, document, pdf_document)
            finally:
                pdf_document.close()
            equations = [block for block in _blocks(new).values() if block.get("type") == "equation"]
            geometry_report["newEquations"] = len(equations)
            geometry_report["equationCrops"] = sum(bool(block.get("src")) for block in equations)
            equation_assets, unplaced_equations, skip_reasons = (
                _add_anchored_equations(old, new) if add_equations else ([], len(equations), {}))
            if add_equations:
                geometry_report["addedEquations"] = len(equation_assets)
                geometry_report["unplacedEquations"] = unplaced_equations
                geometry_report["equationSkipReasons"] = skip_reasons
                geometry_report["blocks"] = len(_blocks(old))
            replacement = old
        else:
            _check_compatibility(old, new, annotations)
            replacement = new
            blocks = _blocks(new)
            geometry_report = {"blocks": len(blocks),
                               "missingBbox": sum(not block.get("bbox") for block in blocks.values())}
        report = {"id": folder.name[:8], "status": "ready-to-upgrade", **geometry_report,
                  "annotations": len(annotations)}
        if backup_root is None:
            return report
        backup = backup_root / folder.name
        if backup.exists():
            raise ValueError("Backup already exists; refusing to overwrite it")
        backup.mkdir(parents=True)
        shutil.copy2(path, backup / "document.json")
        staged_assets = stage / "assets"
        if staged_assets.is_dir() and (not geometry_only or equation_assets):
            assets_to_copy = (staged_assets.iterdir() if not geometry_only else
                              (staged_assets / name for name in equation_assets))
            for asset in assets_to_copy:
                destination = folder / "assets" / asset.name
                if destination.exists():
                    (backup / "assets").mkdir(exist_ok=True)
                    shutil.copy2(destination, backup / "assets" / asset.name)
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(asset, destination)
        temporary_json = folder / "document.upgrade.tmp"
        temporary_json.write_text(json.dumps(replacement, ensure_ascii=False), encoding="utf-8")
        temporary_json.replace(path)
        report["status"] = "upgraded"
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, help="Apply upgrades and save original files here")
    parser.add_argument("--id", action="append", default=[], help="Process only this document ID or unique prefix")
    parser.add_argument("--geometry-only", action="store_true", help="Keep legacy text, IDs, and order; add only proven positions")
    parser.add_argument("--add-equations", action="store_true", help="With --geometry-only, insert cropped equations at exact text anchors")
    args = parser.parse_args()
    if args.add_equations and not args.geometry_only:
        parser.error("--add-equations requires --geometry-only")
    if args.backup_dir and args.backup_dir.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("Backup directory must be outside the document data directory")
    for folder in sorted(args.data_dir.iterdir()):
        if not folder.is_dir() or len(folder.name) != 32 or not (folder / "document.json").is_file():
            continue
        if args.id and not any(folder.name.startswith(prefix) for prefix in args.id):
            continue
        try:
            print(json.dumps(upgrade(folder, args.backup_dir, args.geometry_only, args.add_equations)))
        except Exception as exc:
            print(json.dumps({"id": folder.name[:8], "status": "skipped", "reason": str(exc)}))


if __name__ == "__main__":
    main()
