"""Compare saved PDF models with source captions and inspect reading breaks.

Run in the parser container with /result/corpus-audit/manifest.json present.
This is a read-only audit; it does not change documents or annotations.
"""
import hashlib
import json
import re
from pathlib import Path

import pymupdf
try:
    from backend.app.pdf_fidelity import pdf_figure_caption_blocks
except ModuleNotFoundError:
    from app.pdf_fidelity import pdf_figure_caption_blocks


def audit(root=Path('/userdata'), output=Path('/result/corpus-audit')):
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8-sig'))
    expected = {item['fingerprint']: item for item in manifest}
    candidates = {}
    for source in root.rglob('original.pdf'):
        if '.trash' in source.parts:
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest in expected:
            candidates.setdefault(digest, []).append(source.parent)
    report = []
    for digest, item in expected.items():
        folders = candidates.get(digest, [])
        ready = [folder for folder in folders if (folder / 'document.json').is_file()]
        row = dict(filename=item['filename'], fingerprint=digest)
        if not ready:
            row['status'] = 'pending'
            report.append(row)
            continue
        folder = max(ready, key=lambda path: (path / 'document.json').stat().st_mtime)
        model = json.loads((folder / 'document.json').read_text(encoding='utf-8'))
        blocks = [block for section in model['sections'] for block in section['blocks']]
        figures = [block for block in blocks if block['type'] == 'figure']
        captions = []
        with pymupdf.open(folder / 'original.pdf') as pdf:
            for index, page in enumerate(pdf):
                for entry in pdf_figure_caption_blocks(page):
                    text = re.sub(r'\s+', ' ', entry[4]).strip()
                    match = re.match(r'^Fig(?:ure)?\.?\s*(\d+)\s*[:.]\s*', text, re.I)
                    if match:
                        captions.append(dict(number=int(match[1]), page=index+1, text=text[:160]))
        missing_assets = [b['id'] for b in blocks if b.get('src') and
                          not (folder / 'assets' / b['src'].split('/')[-1]).is_file()]
        numbered = {b.get('number') for b in figures}
        breaks = []
        for section in model['sections']:
            paragraphs = [b for b in section['blocks'] if b['type']=='paragraph' and b.get('text')]
            for first, second in zip(paragraphs, paragraphs[1:]):
                if (not re.search(r'[.!?:;][\]"\u201d\')]*$', first['text'].strip())
                        and re.match(r'^[a-z]', second['text'])):
                    breaks.append(dict(first=first['id'], second=second['id'],
                                       pages=[first.get('page'),second.get('page')],
                                       tail=first['text'][-100:], head=second['text'][:100]))
        row.update(status='ready', id=model['id'], folder=str(folder),
                   source=model.get('source'), pages=model['metadata']['pageCount'],
                   sections=[dict(title=s['title'],level=s['level'],type=s['type']) for s in model['sections']],
                   figures=len(figures), sourceCaptions=captions,
                   missingFigures=[c for c in captions if c['number'] not in numbered],
                   missingAssets=missing_assets, breaks=breaks)
        report.append(row)
    (output/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in report:
        print(row['filename'],row['status'],
              'figures',row.get('figures'), 'missing',len(row.get('missingFigures',[])),
              'breaks',len(row.get('breaks',[])))
    return report


if __name__ == '__main__':
    audit()
