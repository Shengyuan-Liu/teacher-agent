import httpx
import pytest

from app.rag import crawl as crawl_module
from app.rag.crawl import (
    _get_validated,
    demote_headings,
    extract_links,
    in_scope,
    normalise,
    scope_prefix,
)


class TestScope:
    def test_seed_directory_is_the_boundary(self):
        prefix = scope_prefix("https://docs.python.org/3.14/")
        assert prefix == "https://docs.python.org/3.14/"
        assert in_scope("https://docs.python.org/3.14/tutorial/index.html", prefix)
        assert not in_scope("https://docs.python.org/3.13/whatsnew.html", prefix)
        assert not in_scope("https://docs.python.org/", prefix)

    def test_a_file_seed_scopes_to_its_directory(self):
        prefix = scope_prefix("https://example.com/guide/intro.html")
        assert prefix == "https://example.com/guide/"

    def test_other_hosts_are_out(self):
        prefix = scope_prefix("https://comp-lin-alg.github.io/")
        assert not in_scope("https://github.com/comp-lin-alg", prefix)

    def test_assets_are_out_but_linked_pdfs_are_ingestable(self):
        prefix = scope_prefix("https://example.com/")
        assert not in_scope("https://example.com/logo.png", prefix)
        assert in_scope("https://example.com/paper.pdf", prefix)


class TestNormalise:
    def test_drops_fragment_and_query(self):
        assert normalise("https://a.io/p?q=1#top") == "https://a.io/p"

    def test_adds_root_path(self):
        assert normalise("https://a.io") == "https://a.io/"


def test_extract_links_resolves_and_filters():
    html = """
    <a href="tutorial/">tutorial</a>
    <a href="/3.13/old.html">old version</a>
    <a href="https://elsewhere.com/x">external</a>
    <a href="pic.png">image</a>
    <a href="tutorial/">duplicate</a>
    """
    prefix = "https://docs.python.org/3.14/"
    links = extract_links(html, "https://docs.python.org/3.14/index.html", prefix)
    assert links == ["https://docs.python.org/3.14/tutorial/"]


def test_demote_headings_keeps_page_title_on_top():
    assert demote_headings("# Section\n\n## Sub") == "## Section\n\n### Sub"


async def test_redirect_is_validated_before_the_next_request(monkeypatch):
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    def guard(url: str) -> None:
        if "127.0.0.1" in url:
            raise ValueError("private address")

    monkeypatch.setattr(crawl_module, "_assert_public_host", guard)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="private address"):
            await _get_validated(client, "https://example.com/start")
    assert requested == ["https://example.com/start"]
