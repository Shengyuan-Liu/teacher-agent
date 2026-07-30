from app.rag.chunking import CHILD_TARGET, PARENT_TARGET, chunk_document

THEOREM = """\
## Norms

Some preamble about norms.

**Theorem** (Cauchy-Schwarz). For any $\\mathbf{x}, \\mathbf{y} \\in \\mathbb{R}^n$

$$\\left|\\mathbf{x}^\\top \\mathbf{y}\\right| \\leq \\|\\mathbf{x}\\| \\cdot \\|\\mathbf{y}\\|$$

*Proof.* For any $\\lambda \\in \\mathbb{R}$:

$$\\|\\mathbf{x} + \\lambda\\mathbf{y}\\|^2 = \\|\\mathbf{x}\\|^2 + 2\\lambda\\langle x, y\\rangle$$

establishing the result. $\\blacksquare$

A closing remark.
"""


def _all_text(parents) -> str:
    return "\n".join(p.content for p in parents)


def test_theorem_and_proof_stay_together():
    parents = chunk_document(THEOREM)
    holding = [c for p in parents for c in p.children if "Cauchy-Schwarz" in c]
    assert holding, "theorem statement not found in any child"
    # Whichever child carries the statement must also carry its display equation.
    assert any("\\leq \\|\\mathbf{x}\\|" in c for c in holding)


def test_display_math_is_never_split():
    parents = chunk_document(THEOREM)
    for chunk in [p.content for p in parents] + [c for p in parents for c in p.children]:
        assert chunk.count("$$") % 2 == 0, f"unbalanced $$ in: {chunk[:80]!r}"


def test_heading_path_is_tracked():
    text = "# Chapter\n\nintro\n\n## Section\n\nbody text here\n"
    parents = chunk_document(text)
    paths = [p.heading_path for p in parents]
    assert "Chapter" in paths
    assert "Chapter › Section" in paths


def test_children_are_smaller_than_parents():
    text = "## S\n\n" + "\n\n".join(f"Paragraph {i}. " + "x" * 400 for i in range(12))
    parents = chunk_document(text)
    assert parents
    for parent in parents:
        assert len(parent.content) <= PARENT_TARGET + 600
        assert len(parent.children) >= 1
        for child in parent.children:
            assert len(child) <= CHILD_TARGET + 600


def test_children_carry_their_heading_path():
    parents = chunk_document("## Gradient Method\n\nThe step rule is simple.\n")
    child = parents[0].children[0]
    assert child.startswith("Gradient Method")


def test_table_is_not_split():
    rows = "\n".join(f"| a{i} | b{i} |" for i in range(40))
    text = f"## T\n\n| h | h |\n|---|---|\n{rows}\n"
    parents = chunk_document(text)
    tables = [c for p in parents for c in p.children if "| h | h |" in c]
    assert tables, "table header lost"
    assert tables[0].count("|") > 40


def test_code_fence_is_not_split():
    body = "\n".join(f"line_{i} = {i}" for i in range(80))
    parents = chunk_document(f"## Code\n\n```python\n{body}\n```\n")
    for chunk in [c for p in parents for c in p.children]:
        assert chunk.count("```") % 2 == 0


def test_empty_input():
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []


def test_pdf_page_markers_are_preserved_on_parent_chunks():
    text = """<!-- teacher-agent-page:1 -->
# Introduction
First-page material.

<!-- teacher-agent-page:3 -->
# Theorem
Third-page material.
"""
    parents = chunk_document(text)
    assert [parent.page_start for parent in parents] == [1, 3]
    assert [parent.page_end for parent in parents] == [1, 3]
    assert "teacher-agent-page" not in _all_text(parents)


def test_an_overlong_heading_is_trimmed_not_fatal():
    from app.rag.chunking import MAX_HEADING, MAX_HEADING_PATH

    # OCR sometimes puts an annotation blob on a heading line.
    blob = '*preface*[{"box_2d": [186, 418], "caption": "' + "x" * 4000 + '"}]'
    parents = chunk_document(f"# {blob}\n\nBody text under it.\n")
    assert parents
    for parent in parents:
        assert len(parent.heading_path or "") <= MAX_HEADING_PATH
    assert len(parents[0].heading_path) <= MAX_HEADING + 1


def test_deeply_nested_headings_stay_within_the_column():
    from app.rag.chunking import MAX_HEADING_PATH

    text = "".join(f"{'#' * min(i + 1, 6)} {'Section ' + 'n' * 100}\n\nbody\n\n" for i in range(6))
    for parent in chunk_document(text):
        assert len(parent.heading_path or "") <= MAX_HEADING_PATH
