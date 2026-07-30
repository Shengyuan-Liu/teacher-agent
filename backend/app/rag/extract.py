import pypdfium2 as pdfium
from markitdown import MarkItDown

from app.models.source import SourceType
from app.rag.pdf_convert import join_pdf_pages


def extract_text(path: str, source_type: SourceType) -> str:
    """Turn an uploaded file into plain text for chunking.

    PDFs go through pypdfium2 rather than markitdown's pdfminer backend, which
    drops inter-word spaces and emits `(cid:N)` for subset fonts on the kind of
    LaTeX-produced notes users upload.
    """
    if source_type is SourceType.PDF:
        return _extract_pdf(path)
    return MarkItDown().convert(path).markdown


def _extract_pdf(path: str) -> str:
    document = pdfium.PdfDocument(path)
    try:
        pages = [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()
    return join_pdf_pages(pages)
