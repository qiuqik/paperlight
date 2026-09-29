"""Resolve user supplied arXiv URLs to immutable, official version URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


ARXIV_PATH = re.compile(r"^/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?/?$")
USER_AGENT = "Paperlight/0.3 (+https://arxiv.org/help/api/user-manual)"


@dataclass(frozen=True)
class ArxivSource:
    arxiv_id: str
    version: int
    submitted_url: str

    @property
    def versioned_id(self) -> str:
        return f"{self.arxiv_id}v{self.version}"

    @property
    def abs_url(self) -> str:
        return f"https://arxiv.org/abs/{self.versioned_id}"

    @property
    def html_url(self) -> str:
        return f"https://arxiv.org/html/{self.versioned_id}"

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.versioned_id}"


def official_get(client: httpx.Client, url: str, *, max_bytes: int) -> bytes:
    """Fetch only from arxiv.org, including the final redirect target."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "export.arxiv.org"} or parsed.username or parsed.password:
        raise ValueError("Only official arXiv HTTPS resources are allowed.")
    with client.stream("GET", url, follow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("Unexpected arXiv redirect.")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError("arXiv resource exceeds the size limit.")
        return bytes(data)


def resolve_arxiv_url(url: str, client: httpx.Client) -> ArxivSource:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "www.arxiv.org"} or parsed.username or parsed.password or parsed.port:
        raise ValueError("请输入 arxiv.org 的 HTTPS abs、pdf 或 html 链接。")
    match = ARXIV_PATH.fullmatch(parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise ValueError("arXiv 链接格式不正确。")
    identifier, explicit = match.groups()
    if explicit:
        version = int(explicit[1:])
        try:
            official_get(client, f"https://arxiv.org/abs/{identifier}v{version}", max_bytes=2_000_000)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 406:
                raise
            official_get(client, f"https://export.arxiv.org/abs/{identifier}v{version}", max_bytes=2_000_000)
    else:
        try:
            page = official_get(client, f"https://arxiv.org/abs/{identifier}", max_bytes=2_000_000)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 406:
                raise
            page = official_get(client, f"https://export.arxiv.org/abs/{identifier}", max_bytes=2_000_000)
        soup = BeautifulSoup(page, "html.parser")
        history = soup.select_one(".submission-history")
        versions = [int(value) for value in re.findall(r"\[v(\d+)\]", history.get_text(" ", strip=True) if history else "")]
        if not versions:
            raise ValueError("无法从 arXiv 官方版本记录确定版本。")
        version = max(versions)
    return ArxivSource(identifier, version, url.strip())


def fetch_pinned_pdf(source: ArxivSource, client: httpx.Client, max_bytes: int) -> bytes:
    """Try both official PDF hosts while keeping the exact version fixed."""
    try:
        return official_get(client, source.pdf_url, max_bytes=max_bytes)
    except httpx.HTTPStatusError:
        return official_get(client, f"https://export.arxiv.org/pdf/{source.versioned_id}", max_bytes=max_bytes)
