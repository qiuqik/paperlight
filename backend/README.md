# Paperlight local document API

The static Sites deployment cannot run Python parsing workers. This optional local API implements the planned PDF → Docling/GROBID → normalized `DocumentModel` flow while preserving the hosted reader as the frontend. OCR runs automatically only when Docling finds a mostly empty text layer; set `PAPERLIGHT_OCR_MODE=always` or `never` to override this behavior.

## Start the API and parsers

Use Python 3.10–3.13. Docling downloads its model files on first use. GROBID's full Docker image is several gigabytes and can run on CPU; on Apple Silicon Docker Desktop may emulate its Linux amd64 image.

```sh
cd backend
docker compose up --build -d
```

The API is available at `http://127.0.0.1:8000`; interactive API docs are at `/docs`. The API binds to loopback and stores source PDFs and parsed assets in `backend/storage/`. First startup downloads the Docling models and the GROBID full image; the full GROBID image is about 8 GB and runs through amd64 emulation on Apple Silicon. Allow Docker Desktop enough memory for full-text extraction (the GROBID documentation recommends about 4 GB for complete PDF structuring). Model cache is kept in a Docker volume.

To run the API directly on the host instead, install `requirements.txt`, set `GROBID_URL=http://127.0.0.1:8070`, start the GROBID service with `docker compose up -d grobid`, then run `uvicorn app.main:app --host 127.0.0.1 --port 8000`.

## API

- `POST /api/documents` — multipart field `file`, returns a processing document ID.
- `GET /api/documents/{id}` — processing stage or the normalized document JSON.
- `GET /api/documents/{id}/assets/{name}` — extracted figure image.
- `GET /health` — parser availability and GROBID endpoint.

Original PDFs, normalized JSON, raw `docling.json`, raw `grobid.xml`, and extracted assets are stored under `backend/storage/documents/{id}`. Set `PAPERLIGHT_DATA_DIR` to move that local storage. Set `GROBID_URL` if GROBID listens somewhere other than `http://127.0.0.1:8070`. `PAPERLIGHT_CORS_ORIGINS` accepts comma-separated frontend origins; Docker Compose defaults to `http://localhost:8765,http://127.0.0.1:8765`. A direct host run without this setting allows all origins for local development. Uploaded PDFs are limited to 80 MB by default (`MAX_UPLOAD_BYTES`).

GROBID header and citation consolidation are enabled to enrich DOI, publication, and reference metadata when a reliable match is available. Citation matching uses normalized paragraph text and a 0.88 similarity threshold. Unmatched citations are left unresolved; a GROBID outage does not prevent Docling content from being returned.

When GROBID is unreachable or returns no bibliography, the API uses PDFium's text layer to recover numbered references and link numeric in-text callouts. This fallback preserves citation navigation but may omit venue, DOI, or other bibliographic fields.

For the hosted frontend, use the reader settings to set “Parser API URL” to a separately hosted HTTPS API endpoint. The local Docker API binds to loopback for privacy and is intended for the local reader; it is not exposed by the static Sites deployment. Before exposing an API publicly, add authentication, HTTPS, request limits, and restrict `PAPERLIGHT_CORS_ORIGINS` to the reader origin.
