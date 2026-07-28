from app.rag.crawl import demote_headings, extract_links, in_scope, normalise, scope_prefix


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

    def test_binary_and_asset_urls_are_out(self):
        prefix = scope_prefix("https://example.com/")
        assert not in_scope("https://example.com/logo.png", prefix)
        assert not in_scope("https://example.com/paper.pdf", prefix)


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
