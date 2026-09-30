# Paperlight parser API

The parser runs inside the root Docker Compose stack and is reached through the Next.js `/api/parser` proxy. New imports accept PDF uploads and use Docling, optional GROBID, and original-PDF geometry to produce a normalized `DocumentModel`. The arXiv-link import route is retired; saved arXiv documents remain readable. OCR runs automatically only when Docling finds a mostly empty text layer; set `PAPERLIGHT_OCR_MODE=always` or `never` to override this behavior.

## Start the full stack on Windows

Install Docker Desktop with its WSL 2 backend. From the repository root:

```powershell
docker compose up --build -d
docker compose logs parser
```

The Web app listens on `127.0.0.1:8040`. The parser and GROBID have no host ports; the Web app forwards same-origin requests to the parser inside Compose. Each user's source and parsed assets persist in `userdata/users/{user_id}/documents/{document_id}/`; account and annotation records persist in `userdata/paperlight.db`. No parsed source cache is shared between accounts. Docling models persist in a Docker volume. The Docker image installs CPU-only PyTorch and torchvision, and Compose uses GROBID's smaller CPU/CRF image.

For local frontend development only, `backend/docker-compose.yml` can start the parser separately on `127.0.0.1:8000`. Stop the root Compose services before using it, then run these commands from the `backend` directory:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Optional `backend/.env` settings for that development stack:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAPERLIGHT_BIND_HOST` | `127.0.0.1` | Host interface receiving the API. Keep this for a same-machine HTTPS proxy; use `0.0.0.0` only if remote devices must reach the port directly. |
| `PAPERLIGHT_API_PORT` | `8000` | Port on the Windows host. The API's container port remains `8000`. |
| `PAPERLIGHT_CORS_ORIGINS` | `http://localhost:8040,http://127.0.0.1:8040` | Comma-separated browser origins allowed to call the API directly. |

After changing `.env`, run `docker compose up -d --force-recreate`. The development API is at `http://127.0.0.1:<PAPERLIGHT_API_PORT>` and interactive docs are at `/docs`. Use `docker compose logs -f api` to inspect errors and `docker compose down` to stop before resuming the root stack.

## Run without Docker on Windows

Use Python 3.11 or 3.12. In PowerShell, from the `backend` directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use a different installed Python version in the first command if needed. Docling downloads models on first use. GROBID is optional: without a GROBID server, the API still parses the paper and attempts to recover numbered references from the PDF text layer. If you have a separate GROBID service, set `$env:GROBID_URL` before starting Uvicorn. For a published HTTPS reader, set `$env:PAPERLIGHT_CORS_ORIGINS` to the exact reader origin before startup.

## API

- `POST /api/documents/import` (also `/api/documents` and `/parser/jobs`) — multipart field `file`, returns a processing document ID.
- `GET /api/documents/{id}` (also `/parser/jobs/{id}`) — processing stage or the normalized document JSON.
- `GET /api/documents/{id}/model` — the normalized document; returns 409 until parsing succeeds.
- `DELETE /api/documents/{id}` — removes a finished document and its debug snapshot; returns 409 while parsing is active.
- `GET /api/documents` — saved document history.
- `GET /api/library` — PDFs under the configured server library root.
- `POST /api/library/{id}/open` — import a library PDF by its opaque ID; arbitrary paths are not accepted.
- `GET /api/documents/{id}/annotations` — saved highlights and notes.
- `POST /api/documents/{id}/annotations` — create a highlight or note.
- `PATCH /api/documents/{id}/annotations/{annotation_id}` — edit a note.
- `DELETE /api/documents/{id}/annotations/{annotation_id}` — remove one.
- `PATCH /api/annotations/{annotation_id}` and `DELETE /api/annotations/{annotation_id}` — document-independent annotation routes.
- `GET /api/documents/{id}/assets/{name}` — extracted figure image.
- `GET /api/documents/{id}/original.pdf` — the original uploaded or fallback PDF, when present.
- `PATCH /api/documents/{id}/formulas/{block_id}` — JSON `{"revised":"..."}` saves a human TeX correction while preserving the original image and model output.
- `GET /health` — parser availability and GROBID endpoint.

Source HTML or PDF, normalized JSON, raw `docling.json` for PDF, optional `grobid.xml`, extracted assets, and formula provenance are stored in the owner's private document folder with Docker Compose. Set `PAPERLIGHT_DATA_DIR` to move storage for a direct Python run. Set `GROBID_URL` if GROBID listens somewhere other than `http://127.0.0.1:8070`. `PAPERLIGHT_CORS_ORIGINS` accepts comma-separated frontend origins; a direct Python run without this setting allows all origins for local development. Uploaded PDFs are limited to 80 MB by default (`MAX_UPLOAD_BYTES`).

Optional server-only `PAPERLIGHT_DEEPSEEK_API_KEY` enables image transcription with DeepSeek's image-capable `deepseek-flash` model. If absent, `PAPERLIGHT_DOUBAO_API_KEY` and an image-capable `PAPERLIGHT_DOUBAO_VISION_MODEL` enable the Doubao provider. Keep keys in the host environment or an untracked `.env` consumed by root Compose. Transcription is limited to 12 display formulas per document with two concurrent requests and three attempts; failures never block reading. The original PDF crop remains the reading view, and output plus later human corrections are saved separately. Inline PDF formulas are not automatically replaced by model output. alphaXiv MCP is reserved for a later assistant service and is not a body-content source.

For HTML documents imported before the figure-caption adjacency or layout fixes, run `docker compose exec -T parser python -m app.repair_arxiv_figures --id DOCUMENT_ID` from the repository root. It uses the saved official HTML to recover image-only paragraphs immediately preceding a figure caption and restore structured lists, tables, and prompt section classification. It backs up `document.json` and preserves block IDs and annotations.

Place server library PDFs under `userdata/library/` when using Docker Compose. `PAPERLIGHT_LIBRARY_DIR` changes that root for a direct Python run. The library API lists only regular PDF files within the root and returns opaque IDs, not file paths.

Docker Compose also copies each finished or failed parsing attempt to the repository's local `result/users/{owner_id}/{id}/` directory, including its source, status, raw parser output, normalized JSON, and assets. Legacy snapshots may remain in `result/{id}/`. This directory is ignored by Git. For a direct Python run, set `PAPERLIGHT_RESULT_DIR` to enable the same snapshots.

`PAPERLIGHT_REFERENCE_MODE=auto` (default) uses the PDF text layer for clearly numbered bibliographies and skips the slower GROBID pass. Set it to `full` to always run GROBID for richer publication metadata, or `fast` to skip GROBID for every paper. The `status.json` record includes timings for the reference scan, Docling, GROBID, normalization, and the total processing time.

GROBID header and citation consolidation are enabled to enrich DOI, publication, and reference metadata when a reliable match is available. Citation matching uses normalized paragraph text and a 0.88 similarity threshold. Unmatched citations are left unresolved; a GROBID outage does not prevent Docling content from being returned.

When GROBID is unreachable or returns no bibliography, the API uses PDFium's text layer to recover numbered references and link numeric in-text callouts. This fallback preserves citation navigation but may omit venue, DOI, or other bibliographic fields.

The current Tailscale reader uses the same-origin Next.js proxy, so remote browsers only connect to the Web address. Do not expose the parser or GROBID ports for this setup.

## Legacy document models

Models saved before `modelVersion` 3 may have no PDF geometry. `backend/scripts/upgrade_legacy_models.py` rebuilds a model from each document's saved `docling.json` and original PDF without running Docling again. It runs as a dry run unless `--backup-dir` is supplied; `--id` limits it to one document ID or prefix. The upgrade keeps the document ID and refuses changes to existing block IDs, block text, or text order, protecting saved annotation anchors. It can also repair a current model with a missing bbox. With `--backup-dir`, it saves the original `document.json` and any overwritten assets before replacing a compatible model. Documents that fail compatibility checks remain unchanged and require individual review.

For documents that fail full compatibility checks, use `--geometry-only`. It preserves the saved text, IDs, and reading order while copying bbox values only where the rebuilt model or raw Docling snapshot gives a unique exact match. Unmatched blocks remain without bbox and the document stays at its legacy model version. Run without `--backup-dir` to inspect coverage before applying; use a fresh backup directory for each applied run.

`--geometry-only --add-equations` also restores PDF equation crops when the rebuilt model has a crop and both adjacent text anchors map uniquely, in order, to the same saved section. Equations without those anchors are skipped. The original blocks and their IDs stay intact, so previously saved text annotations continue to point to the same content.

`--geometry-only --append-missing-appendix` restores appendix sections lost when an old parser stopped at References. It requires the saved model to have no appendix, the rebuilt appendix to start strictly after all saved block pages, and no section, block, or asset ID conflicts. Run it in a separate pass with its own backup directory. The parser recognizes both explicit “Appendix” headings and lettered headings such as “A Additional Results” after References.
