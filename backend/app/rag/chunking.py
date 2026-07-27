"""Structure-aware chunking with parent/child granularity.

Retrieval wants small passages so the embedding is about one idea; generation
wants the surrounding section so a theorem arrives with its proof. We therefore
build section-sized parents and passage-sized children, embed the children, and
answer from their parents.

Blocks that lose their meaning when cut — display maths, code, tables, and
theorem-like environments — are never split, even when oversized.
"""

import re
from dataclasses import dataclass, field

# A heading is a label. OCR occasionally emits a whole annotation blob on a
# heading line, and an unbounded path then overflows the column and fails the
# whole document.
MAX_HEADING = 120
MAX_HEADING_PATH = 480

PARENT_TARGET = 4000
CHILD_TARGET = 700
CHILD_MIN = 120

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE = re.compile(r"^\s*(```|~~~)")
DISPLAY_MATH = re.compile(r"^\s*\$\$\s*$")
TABLE_ROW = re.compile(r"^\s*\|")
# A theorem-like environment runs until a blank line follows its body, so the
# statement and its proof stay with the label.
ENVIRONMENT = re.compile(
    r"^\s*(?:[*_]{0,2})(Theorem|Lemma|Corollary|Proposition|Definition|Proof|Example|Remark|"
    r"Algorithm|Claim|Exercise)\b",
    re.IGNORECASE,
)


@dataclass
class Block:
    """A unit of text that must not be split."""

    text: str
    atomic: bool = False

    def __len__(self) -> int:
        return len(self.text)


IMAGE_REF = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class ParentChunk:
    heading_path: str | None
    content: str
    children: list[str] = field(default_factory=list)
    #: figure ids referenced in this chunk, in order of appearance
    image_ids: list[str] = field(default_factory=list)


def referenced_images(text: str) -> list[str]:
    seen: list[str] = []
    for _, target in IMAGE_REF.findall(text):
        if target not in seen:
            seen.append(target)
    return seen


def chunk_document(text: str) -> list[ParentChunk]:
    parents: list[ParentChunk] = []
    for heading_path, body in _sections(text):
        for content in _pack(_blocks(body), PARENT_TARGET):
            parent = ParentChunk(heading_path=heading_path, content=content)
            parent.children = _children(content, heading_path)
            parent.image_ids = referenced_images(content)
            parents.append(parent)
    return parents


def _sections(text: str) -> list[tuple[str | None, str]]:
    """Split on headings, tracking the path of enclosing headings."""
    sections: list[tuple[str | None, str]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    path: str | None = None
    in_fence = False

    def flush() -> None:
        body = "\n".join(current).strip()
        if body:
            sections.append((path, body))
        current.clear()

    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            flush()
            level, title = len(match.group(1)), _trim(match.group(2).strip(), MAX_HEADING)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = _trim(" › ".join(title for _, title in stack), MAX_HEADING_PATH)
        else:
            current.append(line)
    flush()
    return sections


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _blocks(body: str) -> list[Block]:
    """Group lines into paragraphs, keeping unsplittable constructs together."""
    lines = body.split("\n")
    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            blocks.append(Block(text))
        buffer.clear()

    i = 0
    while i < len(lines):
        line = lines[i]

        if FENCE.match(line) or DISPLAY_MATH.match(line):
            flush()
            closer = FENCE if FENCE.match(line) else DISPLAY_MATH
            span = [line]
            i += 1
            while i < len(lines):
                span.append(lines[i])
                if closer.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append(Block("\n".join(span), atomic=True))
            continue

        if TABLE_ROW.match(line):
            flush()
            span = []
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                span.append(lines[i])
                i += 1
            blocks.append(Block("\n".join(span), atomic=True))
            continue

        if ENVIRONMENT.match(line):
            flush()
            span = [line]
            i += 1
            # Keep going until a blank line that is not inside display maths.
            depth = 0
            while i < len(lines):
                nxt = lines[i]
                if DISPLAY_MATH.match(nxt):
                    depth ^= 1
                if not nxt.strip() and depth == 0:
                    following = lines[i + 1] if i + 1 < len(lines) else ""
                    # A blank line before more maths still belongs to the block.
                    if not DISPLAY_MATH.match(following):
                        break
                span.append(nxt)
                i += 1
            blocks.append(Block("\n".join(span).strip(), atomic=True))
            continue

        if not line.strip():
            flush()
            i += 1
            continue

        buffer.append(line)
        i += 1

    flush()
    return blocks


def _pack(blocks: list[Block], target: int) -> list[str]:
    """Greedily fill up to `target`, never splitting an atomic block."""
    packed: list[str] = []
    current: list[str] = []
    size = 0

    for block in blocks:
        if current and size + len(block) > target:
            packed.append("\n\n".join(current))
            current, size = [], 0
        if not block.atomic and len(block) > target:
            for piece in _split_sentences(block.text, target):
                if current and size + len(piece) > target:
                    packed.append("\n\n".join(current))
                    current, size = [], 0
                current.append(piece)
                size += len(piece)
            continue
        current.append(block.text)
        size += len(block)

    if current:
        packed.append("\n\n".join(current))
    return packed


def _split_sentences(text: str, target: int) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    pieces: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + len(part) > target:
            pieces.append(current.strip())
            current = part
        else:
            current = f"{current} {part}" if current else part
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _children(content: str, heading_path: str | None) -> list[str]:
    """Passage-sized units for embedding, prefixed with their heading path."""
    pieces = _pack(_blocks(content), CHILD_TARGET)

    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < CHILD_MIN:
            merged[-1] = f"{merged[-1]}\n\n{piece}"
        else:
            merged.append(piece)

    prefix = f"{heading_path}\n\n" if heading_path else ""
    return [prefix + piece for piece in merged]
