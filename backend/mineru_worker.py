"""Private Compose service: run MinerU Basic in its own dependency environment."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
MAX_BYTES = 80 * 1024 * 1024
PARSE_SLOT = asyncio.Semaphore(1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> FileResponse:
    temp = tempfile.TemporaryDirectory(prefix="paperlight-mineru-")
    root = Path(temp.name)
    source, output = root / "source.pdf", root / "result.zip"
    try:
        with source.open("wb") as handle:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="PDF exceeds upload limit.")
                handle.write(chunk)
        with source.open("rb") as handle:
            signature = handle.read(4)
        if signature != b"%PDF":
            raise HTTPException(status_code=422, detail="Expected a PDF file.")
        async with PARSE_SLOT:
            result = await asyncio.to_thread(
                subprocess.run, ["mineru-kit", "parse", str(source), "-o", str(output),
                                 "--format", "zip", "--tier", "basic", "--pages", "all"],
                capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode or not output.is_file():
            raise HTTPException(status_code=502, detail=(result.stderr or result.stdout)[-1500:])
        return FileResponse(output, media_type="application/zip", background=BackgroundTask(temp.cleanup))
    except subprocess.TimeoutExpired as error:
        temp.cleanup()
        raise HTTPException(status_code=504, detail="MinerU parse exceeded 30 minutes.") from error
    except Exception:
        temp.cleanup()
        raise
