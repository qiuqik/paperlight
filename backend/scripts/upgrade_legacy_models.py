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
from backend.app.normalizer import _block_bbox, _heading_key, clean_text, extract_pdf_references, normalize_docling, parse_grobid


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


def _joined_raw_bbox(text: str, page: int, parts: list[tuple[str, int, dict]]) -> dict | None:
    """Find one exact, same-column sequence of raw spans for a merged block."""
    target = clean_text(text)
    if not target:
        return None
    candidates = [(clean_text(value), box) for value, source_page, box in parts
                  if source_page == page and len(clean_text(value)) >= 30]
    matches: list[list[dict]] = []

    def search(offset: int, boxes: list[dict]) -> None:
        if len(matches) > 1 or len(boxes) > 4:
            return
        if offset == len(target):
            if len(boxes) >= 2:
                matches.append(boxes)
            return
        for value, box in candidates:
            if not target.startswith(value, offset) or box in boxes:
                continue
            if boxes:
                previous = boxes[-1]
                overlap = max(0, min(previous["x"] + previous["width"], box["x"] + box["width"])
                              - max(previous["x"], box["x"]))
                if overlap < min(previous["width"], box["width"]) * 0.6:
                    continue
                if box["y"] < previous["y"] - 2 or box["y"] > previous["y"] + previous["height"] + 80:
                    continue
            end = offset + len(value)
            if end < len(target) and target[end] != " ":
                continue
            search(end + (end < len(target)), [*boxes, box])

    search(0, [])
    if len(matches) != 1:
        return None
    boxes = matches[0]
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["width"] for box in boxes)
    bottom = max(box["y"] + box["height"] for box in boxes)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _merge_geometry(old: dict, new: dict, docling_document=None, pdf_document=None) -> dict:
    """Keep legacy content and IDs, copying only unambiguous PDF positions."""
    before, after = _blocks(old), _blocks(new)
    by_signature: dict[tuple, list[dict]] = {}

    def signature(block: dict) -> tuple:
        return (block.get("type"), block.get("text", ""),
                block.get("number"), block.get("caption", ""), tuple(block.get("items", [])))

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
    joined_matched = 0
    if docling_document is not None and pdf_document is not None:
        raw_positions: dict[tuple[str, int], list[dict]] = {}
        raw_parts: list[tuple[str, int, dict]] = []
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
                if str(getattr(getattr(item, "label", None), "value", getattr(item, "label", ""))) in {"text", "list_item"}:
                    raw_parts.append((text, int(page), box))
        for block in before.values():
            if block.get("bbox") or block.get("type") not in {"paragraph", "quote", "footnote", "requirement", "code", "list"}:
                continue
            if old_occurrences[(signature(block), block.get("page"))] != 1:
                continue
            text = clean_text(block.get("text", ""))
            candidates = raw_positions.get((text, block.get("page")), [])
            if len(candidates) == 1:
                block["bbox"] = candidates[0]
                raw_matched += 1
                continue
            merged_text = block.get("text") or " ".join(block.get("items", []))
            joined = _joined_raw_bbox(merged_text, block.get("page"), raw_parts)
            if joined:
                block["bbox"] = joined
                joined_matched += 1
    if not old.get("pages"):
        old["pages"] = new.get("pages", [])
    for collection in ("figures", "tables"):
        for block in old.get(collection, []):
            source = before.get(block.get("id"))
            if source:
                for key in ("page", "bbox", "order"):
                    if key in source:
                        block[key] = source[key]
    return {"matched": matched, "rawMatched": raw_matched, "joinedMatched": joined_matched, "blocks": len(before),
            "missingBbox": sum(not block.get("bbox") for block in before.values()),
            "figuresMissingBbox": sum(block.get("type") == "figure" and not block.get("bbox")
                                      for block in before.values())}


def _embed_equation(old: dict, new_blocks: list[dict], index: int, equation: dict) -> bool:
    """Insert a crop between two raw text fragments without changing anchor text."""
    page = equation.get("page")

    def neighbor(direction: int) -> dict | None:
        for position in range(index + direction, len(new_blocks) if direction > 0 else -1, direction):
            block = new_blocks[position]
            if block.get("type") == "paragraph" and block.get("page") == page and block.get("text"):
                return block
            if block.get("type") != "equation":
                break
        return None

    before, after = neighbor(-1), neighbor(1)
    if not before or not after:
        return False
    before_text, after_text = before["text"], after["text"]
    matches: list[tuple[dict, int]] = []
    for section in old.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") != "paragraph" or block.get("page") != page:
                continue
            text = block.get("text", "")
            first = text.find(before_text)
            second = text.find(after_text)
            if first < 0 or second < 0 or first != text.rfind(before_text) or second != text.rfind(after_text):
                continue
            offset = first + len(before_text)
            if second <= offset or text[offset:second].strip() or second - offset > 2:
                continue
            content = block.get("content") or [{"type": "text", "text": text}]
            rendered = "".join(item.get("display") or item.get("text") or "" for item in content)
            if rendered == text:
                matches.append((block, offset))
    if len(matches) != 1:
        return False
    block, offset = matches[0]
    content = block.get("content") or [{"type": "text", "text": block["text"]}]
    inline = {"type": "inlineEquation", "text": "", "src": equation["src"], "number": equation.get("number")}
    cursor = 0
    for position, node in enumerate(content):
        value = node.get("display") or node.get("text") or ""
        end = cursor + len(value)
        if end == offset:
            insertion = position + 1
            while insertion < len(content) and content[insertion].get("type") == "inlineEquation":
                insertion += 1
            content.insert(insertion, inline)
            block["content"] = content
            return True
        if cursor < offset < end and node.get("type") == "text" and not node.get("display"):
            split = offset - cursor
            content[position:position + 1] = [
                {**node, "text": value[:split]}, inline, {**node, "text": value[split:]},
            ]
            block["content"] = content
            return True
        cursor = end
    return False


def _geometry_equation_anchor(old: dict, equation: dict, section_title: str) -> tuple[int, int] | None:
    """Use a unique gap between adjacent paragraphs on the same PDF page."""
    box = equation.get("bbox")
    page = equation.get("page")
    if not box or not page:
        return None
    title = _heading_key(section_title)
    sections = [(index, section) for index, section in enumerate(old.get("sections", []))
                if _heading_key(section.get("title", "")) == title]
    if len(sections) != 1:
        return None
    section_index, section = sections[0]
    blocks = section.get("blocks", [])
    for block in blocks:
        other = block.get("bbox")
        if block.get("type") != "equation" or block.get("page") != page or not other:
            continue
        overlap = min(box["y"] + box["height"], other["y"] + other["height"]) - max(box["y"], other["y"])
        if overlap > 0 or abs(box["y"] - other["y"]) < 25:
            return None
    candidates: list[tuple[int, int]] = []
    for index in range(1, len(blocks)):
        before, after = blocks[index - 1], blocks[index]
        first, last = before.get("bbox"), after.get("bbox")
        if before.get("type") not in {"paragraph", "figure"} or after.get("type") != "paragraph":
            continue
        if before.get("page") != page or after.get("page") != page or not first or not last:
            continue
        before_bottom = first["y"] + first["height"]
        equation_bottom = box["y"] + box["height"]
        if not (before_bottom + 2 <= box["y"] and equation_bottom + 2 <= last["y"]):
            continue
        if last["y"] - before_bottom > 160:
            continue
        overlaps = [max(0, min(item["x"] + item["width"], box["x"] + box["width"])
                        - max(item["x"], box["x"])) for item in (first, last)]
        if any(overlap < min(item["width"], box["width"]) * 0.25
               for overlap, item in zip(overlaps, (first, last))):
            continue
        candidates.append((section_index, index))
    if section_index + 1 < len(old.get("sections", [])) and blocks:
        before = blocks[-1]
        next_blocks = old["sections"][section_index + 1].get("blocks", [])
        after = next_blocks[0] if next_blocks else None
        first, last = before.get("bbox"), after.get("bbox") if after else None
        if (before.get("type") == "paragraph" and after and after.get("type") == "paragraph"
                and before.get("page") == page and after.get("page") == page and first and last):
            before_bottom = first["y"] + first["height"]
            equation_bottom = box["y"] + box["height"]
            overlaps = [max(0, min(item["x"] + item["width"], box["x"] + box["width"])
                            - max(item["x"], box["x"])) for item in (first, last)]
            if (before_bottom + 2 <= box["y"] and equation_bottom + 2 <= last["y"]
                    and last["y"] - before_bottom <= 160
                    and all(overlap >= min(item["width"], box["width"]) * 0.25
                            for overlap, item in zip(overlaps, (first, last)))):
                candidates.append((section_index, len(blocks)))
    return candidates[0] if len(candidates) == 1 else None


def _add_anchored_equations(old: dict, new: dict) -> tuple[list[str], int, dict[str, int]]:
    """Insert cropped equations only between uniquely identified old neighbors."""
    existing_ids = set(_blocks(old))
    existing_assets = {src for block in _blocks(old).values()
                       for src in [block.get("src"), *(item.get("src") for item in block.get("content", []))] if src}
    planned: dict[int, list[tuple[int, int, dict]]] = {}
    embedded_assets: list[str] = []
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
    new_titles = [section.get("title", "") for section in new.get("sections", []) for _ in section.get("blocks", [])]
    for index, equation in enumerate(new_blocks):
        if equation.get("type") != "equation":
            continue
        if equation.get("src") in existing_assets:
            skip("already-present")
            continue
        if not equation.get("bbox") or not equation.get("src"):
            skip("crop")
            continue
        if _embed_equation(old, new_blocks, index, equation):
            existing_assets.add(equation["src"])
            embedded_assets.append(Path(equation["src"]).name)
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
        issue = ("incomplete-anchors" if not before or not after else
                 "different-sections" if before[0] != after[0] else
                 "conflicting-anchors" if before[1] >= after[1] else
                 "distant-anchor" if abs((before[2] or 0) - (equation.get("page") or 0)) > 1
                 or abs((after[2] or 0) - (equation.get("page") or 0)) > 1 else None)
        if issue:
            geometry = _geometry_equation_anchor(old, equation, new_titles[index])
            if not geometry:
                skip(issue)
                continue
            section_index, insert_at = geometry
        else:
            section_index, insert_at = after[0], after[1]
        added = dict(equation)
        while added["id"] in existing_ids:
            added["id"] = f"added-{added['id']}"
        existing_ids.add(added["id"])
        existing_assets.add(added["src"])
        planned.setdefault(section_index, []).append((insert_at, source_order, added))
        source_order += 1

    assets: list[str] = embedded_assets.copy()
    for section_index, insertions in planned.items():
        blocks = old["sections"][section_index]["blocks"]
        for offset, (index, _source_order, block) in enumerate(sorted(insertions)):
            blocks.insert(index + offset, block)
            assets.append(Path(block["src"]).name)
    for order, block in enumerate(block for section in old.get("sections", []) for block in section.get("blocks", [])):
        block["order"] = order
    return assets, sum(count for reason, count in skipped.items() if reason != "already-present"), skipped


def _append_missing_appendix(old: dict, new: dict) -> tuple[list[str], int, int]:
    """Append a disjoint appendix that the legacy References cutoff lost."""
    if any(section.get("type") == "appendix" for section in old.get("sections", [])):
        return [], 0, 0
    sections = [section for section in new.get("sections", []) if section.get("type") == "appendix"]
    if not sections:
        return [], 0, 0
    blocks = [block for section in sections for block in section.get("blocks", [])]
    pages = [block.get("page") for block in blocks if block.get("page")]
    old_pages = [block.get("page") for section in old.get("sections", [])
                 for block in section.get("blocks", []) if block.get("page")]
    if not pages or not old_pages or min(pages) <= max(old_pages):
        raise ValueError("Appendix pages overlap existing legacy content")
    old_section_ids = {section.get("id") for section in old.get("sections", [])}
    old_blocks = _blocks(old)
    if any(section.get("id") in old_section_ids for section in sections):
        raise ValueError("Appendix section ID collides with existing section")
    if any(block.get("id") in old_blocks for block in blocks):
        raise ValueError("Appendix block ID collides with existing block")
    old_assets = {block.get("src") for block in old_blocks.values() if block.get("src")}
    assets = [Path(block["src"]).name for block in blocks if block.get("src")]
    if any(block.get("src") in old_assets for block in blocks if block.get("src")):
        raise ValueError("Appendix asset collides with an existing asset")
    old.setdefault("sections", []).extend(sections)
    for collection in ("figures", "tables"):
        appended_ids = {block["id"] for block in blocks if block.get("type") == collection[:-1]}
        if appended_ids:
            old.setdefault(collection, []).extend(
                block for block in new.get(collection, []) if block.get("id") in appended_ids)
    for order, block in enumerate(block for section in old["sections"] for block in section.get("blocks", [])):
        block["order"] = order
    return assets, len(sections), len(blocks)


def upgrade(folder: Path, backup_root: Path | None, geometry_only: bool = False,
            add_equations: bool = False, append_missing_appendix: bool = False) -> dict:
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
            appendix_assets, appended_sections, appended_blocks = (
                _append_missing_appendix(old, new) if append_missing_appendix else ([], 0, 0))
            if append_missing_appendix:
                geometry_report["appendedSections"] = appended_sections
                geometry_report["appendedBlocks"] = appended_blocks
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
        if staged_assets.is_dir() and (not geometry_only or equation_assets or appendix_assets):
            assets_to_copy = (staged_assets.iterdir() if not geometry_only else
                              (staged_assets / name for name in dict.fromkeys([*equation_assets, *appendix_assets])))
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
    parser.add_argument("--append-missing-appendix", action="store_true", help="With --geometry-only, append a nonoverlapping lost appendix")
    args = parser.parse_args()
    if (args.add_equations or args.append_missing_appendix) and not args.geometry_only:
        parser.error("Equation and appendix recovery require --geometry-only")
    if args.add_equations and args.append_missing_appendix:
        parser.error("Run equation recovery and appendix recovery in separate backed-up passes")
    if args.backup_dir and args.backup_dir.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("Backup directory must be outside the document data directory")
    for folder in sorted(args.data_dir.iterdir()):
        if not folder.is_dir() or len(folder.name) != 32 or not (folder / "document.json").is_file():
            continue
        if args.id and not any(folder.name.startswith(prefix) for prefix in args.id):
            continue
        try:
            print(json.dumps(upgrade(folder, args.backup_dir, args.geometry_only,
                                     args.add_equations, args.append_missing_appendix)))
        except Exception as exc:
            print(json.dumps({"id": folder.name[:8], "status": "skipped", "reason": str(exc)}))


if __name__ == "__main__":
    main()
