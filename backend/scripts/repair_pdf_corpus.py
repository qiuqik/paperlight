"""Repair audited saved PDFs with backups, without replacing annotated text."""
import argparse
import json
import shutil
from pathlib import Path

from backend.app.model import DocumentModel
from backend.app.normalizer import finalize_document_model
from backend.app.pdf_fidelity import recover_missing_pdf_figures, repair_pdf_word_spacing, expand_pdf_figure_crops
from backend.app.pdf_frontmatter import repair_pdf_frontmatter
from backend.scripts.upgrade_legacy_models import _annotation_blocks


def repair(folder: Path, backup_root: Path | None):
    path = folder / 'document.json'
    original = path.read_text(encoding='utf-8')
    model = DocumentModel.model_validate_json(original)
    if not model.source.startswith('docling'):
        return dict(id=model.id, skipped='not a Docling PDF')
    annotations_path = folder / 'annotations.json'
    annotations = json.loads(annotations_path.read_text(encoding='utf-8')) if annotations_path.exists() else []
    protected = set().union(*(_annotation_blocks(a) for a in annotations)) if annotations else set()
    before = {b.id: b.model_dump() for s in model.sections for b in s.blocks if b.id in protected}
    # Crop into a staging folder even for dry runs. Original assets stay untouched.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='paperlight-repair-') as temp:
        staging = Path(temp)
        recovered = recover_missing_pdf_figures(model, folder/'original.pdf', staging,protected)
        expanded = expand_pdf_figure_crops(model,folder/'original.pdf',staging,protected)
        frontmatter = repair_pdf_frontmatter(model,folder/'original.pdf',protected)
        spacing = repair_pdf_word_spacing(model,folder/'original.pdf',protected)
        model = finalize_document_model(model, protected, folder)
        after = {b.id: b.model_dump() for s in model.sections for b in s.blocks if b.id in protected}
        for key, block in before.items():
            if key not in after or any(after[key].get(field) != block.get(field) for field in ('text','content','page','bbox')):
                raise ValueError(f'Annotated anchor would change: {key}')
        report = dict(id=model.id, recoveredFigures=recovered, expandedFigures=expanded, frontmatter=frontmatter, wordSpacing=spacing,
                      continuations=sum(b.continuesPrevious for s in model.sections for b in s.blocks),
                      annotations=len(annotations), applied=bool(backup_root))
        if backup_root:
            backup = backup_root / model.id
            backup.mkdir(parents=True, exist_ok=True)
            if not (backup/'document.json').exists():
                (backup/'document.json').write_text(original,encoding='utf-8')
            for asset in (staging/'assets').glob('*'):
                destination = folder/'assets'/asset.name
                if destination.exists() and not (backup/'assets'/asset.name).exists():
                    (backup/'assets').mkdir(exist_ok=True)
                    shutil.copy2(destination,backup/'assets'/asset.name)
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(asset,destination)
            temporary = path.with_suffix('.repair.tmp')
            temporary.write_text(model.model_dump_json(),encoding='utf-8')
            temporary.replace(path)
            from backend.app.main import ACCOUNTS
            ACCOUNTS.update_document(model.id,status='ready',title=model.metadata.title,
                                     authors=model.metadata.authors,page_count=model.metadata.pageCount)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit',type=Path,default=Path('/result/corpus-audit/audit.json'))
    parser.add_argument('--backup-dir',type=Path)
    args = parser.parse_args()
    for row in json.loads(args.audit.read_text(encoding='utf-8')):
        if row['status'] == 'ready':
            print(json.dumps(repair(Path(row['folder']), args.backup_dir)), flush=True)
