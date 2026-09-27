"""Serve the built reader without caching its HTML or JavaScript."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class PaperlightHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.split("?", 1)[0].endswith(("/", ".html", ".js", ".json", ".css")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    dist = Path(__file__).resolve().parents[1] / "dist"
    handler = partial(PaperlightHandler, directory=str(dist))
    with ThreadingHTTPServer(("127.0.0.1", 8765), handler) as server:
        server.serve_forever()
