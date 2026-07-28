"""Same-site crawling for URL sources.

The seed's directory is the boundary: starting at docs.python.org/3.14/ must
not wander into /3.13/ or the rest of the host. Pages become one markdown
document, each page a top-level section, so heading paths and citations name
the page a passage came from.
"""

import asyncio
import ipaddress
import re
import socket
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
import structlog
import trafilatura
from bs4 import BeautifulSoup

from app.core.config import settings

log = structlog.get_logger()

PAGE_TIMEOUT = 20
SKIP_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".zip",
    ".tar",
    ".gz",
    ".whl",
    ".exe",
    ".mp4",
    ".webm",
    ".woff",
    ".woff2",
    ".pdf",
    ".epub",
)

ProgressCallback = Callable[[float, str], Awaitable[None]]


@dataclass
class Page:
    url: str
    title: str
    markdown: str


def scope_prefix(seed: str) -> str:
    """The directory the crawl may not leave."""
    parts = urlsplit(seed)
    path = parts.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def normalise(url: str) -> str:
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def in_scope(url: str, prefix: str) -> bool:
    if not url.startswith(prefix):
        return False
    return not urlsplit(url).path.lower().endswith(SKIP_EXTENSIONS)


def extract_links(html: str, base_url: str, prefix: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        target = normalise(urljoin(base_url, anchor["href"]))
        if in_scope(target, prefix) and target not in found:
            found.append(target)
    return found


def page_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback


def demote_headings(markdown: str) -> str:
    """Push page-internal headings one level down so the page title stays top."""
    return re.sub(r"^(#{1,5})(\s)", r"#\1\2", markdown, flags=re.MULTILINE)


def _assert_public_host(url: str) -> None:
    if not settings.crawl_block_private_addresses:
        return
    host = urlsplit(url).hostname or ""
    infos = socket.getaddrinfo(host, None)
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError(f"Refusing to crawl non-public address {address} for {host}")


async def _robots(client: httpx.AsyncClient, seed: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parts = urlsplit(seed)
    try:
        response = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
        parser.parse(response.text.splitlines() if response.status_code == 200 else [])
    except httpx.HTTPError:
        parser.parse([])
    return parser


async def crawl(
    seed: str,
    max_pages: int | None = None,
    report: ProgressCallback | None = None,
) -> list[Page]:
    limit = min(max_pages or settings.max_crawl_pages, settings.max_crawl_pages)
    seed = normalise(seed)
    prefix = scope_prefix(seed)
    await asyncio.to_thread(_assert_public_host, seed)

    pages: list[Page] = []
    seen = {seed}
    queue: list[tuple[str, int]] = [(seed, 0)]

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=PAGE_TIMEOUT,
        headers={"User-Agent": "TeacherAgent/0.1 (learning assistant; contact: local)"},
    ) as client:
        robots = await _robots(client, seed)

        while queue and len(pages) < limit:
            url, depth = queue.pop(0)
            if not robots.can_fetch("*", url):
                continue
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                log.info("crawl.page_failed", url=url, error=str(exc))
                continue

            final_url = normalise(str(response.url))
            if response.status_code != 200 or not in_scope(final_url, prefix):
                continue
            if "text/html" not in response.headers.get("content-type", ""):
                continue

            html = response.text
            markdown = trafilatura.extract(
                html, output_format="markdown", include_tables=True, include_links=False
            )
            if markdown and markdown.strip():
                pages.append(
                    Page(
                        url=final_url,
                        title=page_title(html, final_url),
                        markdown=demote_headings(markdown.strip()),
                    )
                )
                if report:
                    await report(len(pages) / limit, f"Crawled {len(pages)}/{limit} pages")

            if depth < settings.max_crawl_depth:
                for link in extract_links(html, final_url, prefix):
                    if link not in seen:
                        seen.add(link)
                        queue.append((link, depth + 1))

    if not pages:
        raise ValueError("The crawl produced no readable pages")
    log.info("crawl.done", seed=seed, pages=len(pages))
    return pages


def pages_to_markdown(pages: list[Page]) -> str:
    sections = [f"# {p.title}\n\nSource: {p.url}\n\n{p.markdown}" for p in pages]
    return "\n\n".join(sections)
