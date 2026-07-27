"""PDF to Markdown conversion.

Plain text extraction destroys mathematics: a rendered `‖x‖²` comes back as
`‖x‖` and `2` on separate lines, so formulas are unusable for both retrieval
and display. These converters return Markdown with LaTeX instead.
"""

import asyncio
import base64
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
import pypdfium2 as pdfium
import structlog

from app.core.config import settings

log = structlog.get_logger()

PAGE_PROMPT = """Transcribe this page of a document into GitHub-flavored Markdown.

Rules:
- All mathematics in LaTeX: $...$ inline, $$...$$ on their own lines for display.
- Preserve the heading hierarchy with #, ##, ###.
- Keep Theorem / Definition / Proof / Example environments as a bold label
  followed by their text, on their own paragraph.
- Reproduce tables as Markdown tables.
- Transcribe only what is on the page. Do not add commentary, and do not wrap
  the page in a code fence."""

PAGE_SEPARATOR = "\n\n"


@dataclass
class ConvertedDocument:
    markdown: str
    #: figure id (as referenced by `![id](id)` in the markdown) -> raw bytes
    images: dict[str, bytes] = field(default_factory=dict)


class PdfConverter(Protocol):
    async def convert(self, path: str) -> ConvertedDocument: ...


class TextConverter:
    """No API calls: fast and free, but mathematics comes out mangled."""

    async def convert(self, path: str) -> ConvertedDocument:
        document = pdfium.PdfDocument(path)
        try:
            pages = [page.get_textpage().get_text_range() for page in document]
        finally:
            document.close()
        return ConvertedDocument(PAGE_SEPARATOR.join(p.strip() for p in pages if p.strip()))


class MistralOcrConverter:
    """Mistral's document OCR: one request for the whole file, Markdown back."""

    URL = "https://api.mistral.ai/v1/ocr"

    async def convert(self, path: str) -> ConvertedDocument:
        raw = await asyncio.to_thread(Path(path).read_bytes)
        encoded = base64.b64encode(raw).decode()

        async with httpx.AsyncClient(timeout=settings.pdf_convert_timeout) as client:
            response = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
                json={
                    "model": settings.mistral_ocr_model,
                    "document": {
                        "type": "document_url",
                        "document_url": f"data:application/pdf;base64,{encoded}",
                    },
                    "include_image_base64": True,
                },
            )
            response.raise_for_status()
            payload = response.json()

        markdown, images = [], {}
        for page in payload.get("pages", []):
            if page.get("markdown", "").strip():
                markdown.append(page["markdown"].strip())
            for image in page.get("images", []):
                encoded_image = image.get("image_base64")
                if image.get("id") and encoded_image:
                    images[image["id"]] = base64.b64decode(encoded_image.split(",")[-1])
        return ConvertedDocument(PAGE_SEPARATOR.join(markdown), images)


class GeminiConverter:
    """Renders each page and transcribes it with a cheap multimodal model."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models"

    async def convert(self, path: str) -> ConvertedDocument:
        renders = await asyncio.to_thread(_render_pages, path)
        async with httpx.AsyncClient(timeout=settings.pdf_convert_timeout) as client:
            pages = [await self._page(client, image) for image in renders]
        return ConvertedDocument(PAGE_SEPARATOR.join(p.strip() for p in pages if p.strip()))

    async def _page(self, client: httpx.AsyncClient, image: bytes) -> str:
        response = await client.post(
            f"{self.URL}/{settings.gemini_vision_model}:generateContent",
            params={"key": settings.gemini_api_key},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": PAGE_PROMPT},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": base64.b64encode(image).decode(),
                                }
                            },
                        ]
                    }
                ]
            },
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            return ""
        return "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])


def _render_pages(path: str, scale: float = 2.0) -> list[bytes]:
    document = pdfium.PdfDocument(path)
    try:
        images = []
        for page in document:
            buffer = io.BytesIO()
            page.render(scale=scale).to_pil().save(buffer, format="PNG")
            images.append(buffer.getvalue())
        return images
    finally:
        document.close()


def get_converter() -> PdfConverter:
    match settings.pdf_converter:
        case "mistral":
            return MistralOcrConverter()
        case "gemini":
            return GeminiConverter()
        case _:
            return TextConverter()
