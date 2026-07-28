import pytest

from app.rag.repo import SECTION_CHARS, _file_sections, _select_files, _windows, parse_repo_url


class TestParseRepoUrl:
    def test_accepts_the_plain_form(self):
        assert parse_repo_url("https://github.com/karpathy/nanochat") == ("karpathy", "nanochat")

    def test_accepts_git_suffix_and_trailing_slash(self):
        assert parse_repo_url("https://github.com/a/b.git") == ("a", "b")
        assert parse_repo_url("https://github.com/a/b/") == ("a", "b")

    def test_rejects_other_hosts_and_shapes(self):
        for bad in (
            "https://gitlab.com/a/b",
            "https://github.com/only-owner",
            "http://github.com/a/b",
            "https://github.com/a/b/tree/main",
        ):
            with pytest.raises(ValueError):
                parse_repo_url(bad)


def test_select_files_applies_the_filters(tmp_path):
    (tmp_path / "keep.py").write_text("print('hi')")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "uv.lock").write_text("locked")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "binary.py").write_bytes(b"\x00\x01\x02")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("junk")
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 100_000)

    names = {p.name for p in _select_files(tmp_path)}
    assert names == {"keep.py", "README.md"}


def test_code_files_are_fenced_and_windowed(tmp_path):
    path = tmp_path / "model.py"
    path.write_text("\n".join(f"line_{i} = {i}" for i in range(600)))
    sections = _file_sections(tmp_path, path)
    assert len(sections) > 1
    for section in sections:
        assert section.count("```") % 2 == 0
        assert section.startswith("# model.py (part ")


def test_windows_respect_the_size_cap():
    text = "\n".join("y" * 80 for _ in range(200))
    for window in _windows(text):
        assert len(window) <= SECTION_CHARS + 81


def test_markdown_files_keep_their_content_demoted(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Title\n\nBody")
    (section,) = _file_sections(tmp_path, path)
    assert section.startswith("# guide.md\n")
    assert "## Title" in section
