"""GitHub repository ingestion.

A shallow clone is flattened into one markdown document: repository tree, then
README and docs, then code files as fenced sections. Large code files are cut
into line windows before fencing, because the chunker treats a fence as atomic
and an unsplit 3000-line file would exceed the embedding input limit.
"""

import re
import shutil
import tempfile
from pathlib import Path

import structlog
from git import Repo

from app.core.config import settings

log = structlog.get_logger()

GITHUB_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".idea",
    ".vscode",
    "assets",
}
LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "cargo.lock",
    "gemfile.lock",
    "composer.lock",
}
DOC_EXTENSIONS = {".md", ".rst", ".txt"}
CODE_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".sql": "sql",
    ".swift": "swift",
    ".scala": "scala",
    ".lua": "lua",
    ".r": "r",
    ".jl": "julia",
    ".cs": "csharp",
    ".html": "html",
    ".css": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
}
MAX_FILE_BYTES = 200_000
SECTION_CHARS = 3500


def parse_repo_url(url: str) -> tuple[str, str]:
    match = GITHUB_URL.match(url.strip())
    if not match:
        raise ValueError("Expected a repository URL like https://github.com/owner/repo")
    return match.group(1), match.group(2)


def repo_to_markdown(repo_url: str) -> str:
    owner, name = parse_repo_url(repo_url)
    with tempfile.TemporaryDirectory(prefix="teacher-agent-clone-") as workdir:
        clone_dir = Path(workdir) / name
        log.info("repo.cloning", url=repo_url)
        Repo.clone_from(repo_url, clone_dir, depth=1, single_branch=True)
        shutil.rmtree(clone_dir / ".git", ignore_errors=True)

        files = _select_files(clone_dir)
        total = sum(path.stat().st_size for path in files)
        if total > settings.max_repo_size_mb * 1024 * 1024:
            raise ValueError(f"Repository text exceeds the {settings.max_repo_size_mb} MB limit")

        sections = [_tree_section(owner, name, clone_dir, files)]
        for path in _reading_order(clone_dir, files):
            sections.extend(_file_sections(clone_dir, path))
    log.info("repo.flattened", url=repo_url, files=len(files))
    return "\n\n".join(sections)


def _select_files(root: Path) -> list[Path]:
    chosen: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        name = path.name.lower()
        if name in LOCKFILES or name.endswith(".min.js"):
            continue
        suffix = path.suffix.lower()
        if suffix not in DOC_EXTENSIONS and suffix not in CODE_LANGUAGES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:1024]:
            continue
        chosen.append(path)
    return chosen


def _reading_order(root: Path, files: list[Path]) -> list[Path]:
    def rank(path: Path) -> tuple[int, str]:
        relative = str(path.relative_to(root)).lower()
        if path.name.lower().startswith("readme"):
            return (0, relative)
        if path.suffix.lower() in DOC_EXTENSIONS:
            return (1, relative)
        return (2, relative)

    return sorted(files, key=rank)


def _tree_section(owner: str, name: str, root: Path, files: list[Path]) -> str:
    listing = "\n".join(str(path.relative_to(root)) for path in files)
    return f"# Repository {owner}/{name}\n\nFiles included in this snapshot:\n\n```\n{listing}\n```"


def _file_sections(root: Path, path: Path) -> list[str]:
    relative = path.relative_to(root)
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []

    if path.suffix.lower() in DOC_EXTENSIONS:
        body = re.sub(r"^(#{1,5})(\s)", r"#\1\2", text, flags=re.MULTILINE)
        return [f"# {relative}\n\n{body}"]

    language = CODE_LANGUAGES.get(path.suffix.lower(), "")
    fence = "````" if "```" in text else "```"
    windows = _windows(text)
    sections = []
    for index, window in enumerate(windows, 1):
        part = f" (part {index}/{len(windows)})" if len(windows) > 1 else ""
        sections.append(f"# {relative}{part}\n\n{fence}{language}\n{window}\n{fence}")
    return sections


def _windows(text: str) -> list[str]:
    if len(text) <= SECTION_CHARS:
        return [text]
    windows: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if current and size + len(line) > SECTION_CHARS:
            windows.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        windows.append("\n".join(current))
    return windows
