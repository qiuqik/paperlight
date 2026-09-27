# Paperlight local document API

The static Sites deployment cannot run Python parsing workers. This optional local API implements the planned PDF → Docling/GROBID → normalized `DocumentModel` flow while preserving the hosted reader as the frontend. OCR runs automatically only when Docling finds a mostly empty text layer; set `PAPERLIGHT_OCR_MODE=always` or `never` to override this behavior.

## Start with Docker on Windows

Install Docker Desktop with its WSL 2 backend. In PowerShell, from the `backend` directory:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
curl.exe http://127.0.0.1:8000/health
```

Edit `backend/.env` to set:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAPERLIGHT_BIND_HOST` | `127.0.0.1` | Host interface receiving the API. Keep this for a same-machine HTTPS proxy; use `0.0.0.0` only if remote devices must reach the port directly. |
| `PAPERLIGHT_API_PORT` | `8000` | Port on the Windows host. The API's container port remains `8000`. |
| `PAPERLIGHT_CORS_ORIGINS` | `http://localhost:8765,http://127.0.0.1:8765` | Comma-separated browser origins allowed to call the API. Add the exact HTTPS reader origin when using the published website. |

After changing `.env`, run `docker compose up -d --force-recreate`. The API is at `http://127.0.0.1:<PAPERLIGHT_API_PORT>` and interactive docs are at `/docs`. GROBID stays inside the Compose network and has no published host port. Source PDFs, parsed assets, history, and notes persist in the repository's `userdata/documents/`; Docling models persist in a Docker volume. The Docker image installs CPU-only PyTorch and torchvision, and Compose uses GROBID's smaller CPU/CRF image. CRF uses less disk and memory than the full image, with some loss of citation and metadata extraction accuracy. First startup downloads the Docling models and GROBID image and may take some time. Use `docker compose logs -f api` to inspect errors and `docker compose down` to stop.

## Run without Docker on Windows

Use Python 3.11 or 3.12. In PowerShell, from the `backend` directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use a different installed Python version in the first command if needed. Docling downloads models on first use. GROBID is optional: without a GROBID server, the API still parses the paper and attempts to recover numbered references from the PDF text layer. If you have a separate GROBID service, set `$env:GROBID_URL` before starting Uvicorn. For a published HTTPS reader, set `$env:PAPERLIGHT_CORS_ORIGINS` to the exact reader origin before startup.

## API

- `POST /api/documents` — multipart field `file`, returns a processing document ID.
- `GET /api/documents/{id}` — processing stage or the normalized document JSON.
- `GET /api/documents` — saved document history.
- `GET /api/documents/{id}/annotations` — saved highlights and notes.
- `POST /api/documents/{id}/annotations` — create a highlight or note.
- `PATCH /api/documents/{id}/annotations/{annotation_id}` — edit a note.
- `DELETE /api/documents/{id}/annotations/{annotation_id}` — remove one.
- `GET /api/documents/{id}/assets/{name}` — extracted figure image.
- `GET /health` — parser availability and GROBID endpoint.

Original PDFs, normalized JSON, raw `docling.json`, optional `grobid.xml`, extracted assets, and `annotations.json` are stored under `userdata/documents/{id}` with Docker Compose. Set `PAPERLIGHT_DATA_DIR` to move storage for a direct Python run. Set `GROBID_URL` if GROBID listens somewhere other than `http://127.0.0.1:8070`. `PAPERLIGHT_CORS_ORIGINS` accepts comma-separated frontend origins; a direct Python run without this setting allows all origins for local development. Uploaded PDFs are limited to 80 MB by default (`MAX_UPLOAD_BYTES`).

Docker Compose also copies each finished or failed parsing attempt to the repository's local `result/{id}/` directory, including the source PDF, status, raw Docling/GROBID output, normalized JSON, and figure assets. This directory is ignored by Git so it can hold examples for parser debugging. For a direct Python run, set `PAPERLIGHT_RESULT_DIR` to enable the same snapshots.

`PAPERLIGHT_REFERENCE_MODE=auto` (default) uses the PDF text layer for clearly numbered bibliographies and skips the slower GROBID pass. Set it to `full` to always run GROBID for richer publication metadata, or `fast` to skip GROBID for every paper. The `status.json` record includes timings for the reference scan, Docling, GROBID, normalization, and the total processing time.

GROBID header and citation consolidation are enabled to enrich DOI, publication, and reference metadata when a reliable match is available. Citation matching uses normalized paragraph text and a 0.88 similarity threshold. Unmatched citations are left unresolved; a GROBID outage does not prevent Docling content from being returned.

When GROBID is unreachable or returns no bibliography, the API uses PDFium's text layer to recover numbered references and link numeric in-text callouts. This fallback preserves citation navigation but may omit venue, DOI, or other bibliographic fields.

For the hosted frontend, use the reader settings to set “Parser API URL” to a separately hosted HTTPS API endpoint. The static Sites deployment does not host this Python API. A public endpoint needs authentication, HTTPS, request limits, and a `PAPERLIGHT_CORS_ORIGINS` value including the reader origin. An HTTPS proxy on the Windows host can forward to the default loopback binding without publishing the API port directly.
