"""Repair previously imported HTML figures whose image precedes their caption."""

from __future__ import annotations

import argparse
import json

import httpx

from . import main
from .arxiv_html import repair_missing_figure_assets
from .arxiv_source import ArxivSource
from .model import DocumentModel


def repair(document_id: str) -> int:
    record = main.ACCOUNTS.document(document_id)
    if not record:
        raise ValueError("Document not found.")
    folder = main._document_folder(document_id)
    path = folder / "document.json"
    html_path = folder / "official.html"
    model = DocumentModel(**json.loads(path.read_text(encoding="utf-8")))
    if model.source != "arxiv_html" or not model.arxivId or not model.arxivVersion or not html_path.is_file():
        raise ValueError("Document is not a saved arXiv HTML import.")
    source = ArxivSource(model.arxivId, model.arxivVersion, model.sourceUrl)
    with httpx.Client(headers={"User-Agent": "Paperlight/0.3"}, timeout=30) as client:
        count = repair_missing_figure_assets(model, html_path.read_bytes(), source, folder, client)
    if count:
        backup = folder / "document.json.before-figure-repair"
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        main._atomic_json(path, model.model_dump(mode="json"))
        main._save_result(document_id)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True, help="Existing arXiv HTML document ID")
    args = parser.parse_args()
    print(f"Recovered {repair(args.id)} figure image(s).")
