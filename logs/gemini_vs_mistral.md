# Gemini vs. Mistral PDF-to-Markdown Evaluation

## Verdict

**Mistral OCR 4 is the better production parser for this PDF.** It completed the
entire 132-page document with stable output, preserved and returned all 61
referenced figures, produced a usable table of contents, and finished 2.23 times
faster than the working Gemini comparison.

Gemini produced cleaner LaTeX and was more accurate on several sampled matrices,
but its current project configuration cannot run at all. After changing only the
test model and authentication transport, Gemini completed all pages but generated
a catastrophic 196,611-character repetition on the table-of-contents page and did
not return any image bytes. It should not replace Mistral as the default converter
until those failures are bounded.

## Test subject and environment

- Date: 2026-07-31
- Repository revision: `76be8d2`
- PDF: `backend/storage/95ef4f75-b1b1-43a3-91f0-03d2df242def/db18d128-bd89-4c73-b0b0-ef36d9f8796e.pdf`
- Document: *MATH60045 and MATH70045 Applied Probability*, Autumn 2025
- Size: 2,064,428 bytes
- Pages: 132 A4 pages
- Source type: digitally generated pdfTeX document with selectable text,
  mathematical notation, tables, plots, diagrams, definitions, theorems, and
  proofs
- Mistral model: project-configured `mistral-ocr-4`
- Gemini model configured by the project: `gemini-2.5-flash-lite`
- Gemini model used for the quality-only comparison: `gemini-3.5-flash-lite`
- Gemini page rendering: project default, 2x scale PNG
- Gemini transcription prompt: the unmodified `PAGE_PROMPT` from
  `backend/app/rag/pdf_convert.py`
- Repository timeout setting: 600 seconds per HTTP client operation

The worktree already contained unrelated changes when this evaluation started;
they were not modified. API keys and raw API responses containing credentials are
intentionally omitted from this report.

## Important configuration findings

### Current Gemini path does not work

The unmodified `GeminiConverter` failed on its first page with HTTP 404. Google's
error stated that `gemini-2.5-flash-lite` is no longer available to new users and
that a newer model must be selected. This is a model-lifecycle failure, not a PDF
quality failure.

The account's model listing included `gemini-3.5-flash-lite`, so the quality test
was repeated with that model. No prompt, image resolution, page order, or parsing
logic was changed.

### Gemini authentication can leak the API key into error logs

The current implementation supplies the Gemini key through the URL query string:

```python
params={"key": settings.gemini_api_key}
```

When `httpx.raise_for_status()` raised the 404, the exception included the complete
request URL and therefore the credential. The successful comparison used the
equivalent `x-goog-api-key` request header so that failures did not place the key
in the URL. The project implementation should be changed to header authentication,
and any key exposed through logs should be rotated.

### `.env` currently blocks normal settings initialization

The local `.env` contains `DEBUG=release`, while `Settings.debug` is Boolean. A
normal import failed Pydantic validation before either converter could start. The
evaluation commands used a process-local `DEBUG=false` override and did not edit
the user's `.env`.

## Method

Both working runs parsed the complete PDF. The following measurements were then
calculated from the returned `ConvertedDocument`:

1. Page-marker coverage and missing page numbers.
2. Wall-clock conversion time.
3. Output size and basic Markdown structure counts.
4. Extracted image references and returned image byte payloads.
5. Replacement-character count.
6. Per-page source-word recall against the PDF's selectable text layer. This is a
   coverage signal, not a complete correctness score: it does not validate LaTeX
   structure or figures and can penalize legitimate normalization.
7. Manual comparison against the rendered PDF on pages 1, 2, 6, 19, 31, 43, 67,
   80, 94, 119, and 132. These samples cover the title, contents, prose, matrices,
   theorems and proofs, dense equations, figures, and bibliography.

Counts such as headings, table rows, and LaTeX commands describe output shape;
they are not interpreted as “more is always better.”

## Whole-document results

| Metric | Mistral OCR 4 | Gemini 3.5 Flash-Lite | Interpretation |
| --- | ---: | ---: | --- |
| Successful pages | 132/132 | 132/132 | Both working runs had full page coverage |
| Missing pages | 0 | 0 | No blank/missing page markers |
| Wall time | 273.69 s | 610.21 s | Current serial Gemini path was 2.23x slower |
| Markdown characters | 329,653 | 529,735 | Gemini total is inflated by page 2 repetition |
| Characters excluding Gemini page 2 | 329,653 | 333,124 | Normal pages have comparable output volume |
| Mean source-word recall | 95.45% | 94.80% | Mistral leads by 0.65 percentage points |
| Median source-word recall | 95.92% | 95.86% | Typical-page coverage is effectively tied |
| Markdown headings | 135 | 120 | Both retain substantial hierarchy |
| Markdown table rows | 111 | 4 | Mistral represented the contents as a usable table |
| Display blocks using `$$...$$` | 360 | 741 | Not directly comparable; Mistral also uses `\[...\]` |
| LaTeX commands | 8,648 | 43,238 | Gemini count is heavily inflated by repeated `\dots` |
| Image references | 61 | 15 | Gemini references are mostly unresolved placeholders |
| Returned image payloads | 61 | 0 | Only Mistral preserves usable figures |
| Unicode replacement characters | 0 | 0 | Neither output showed decoding corruption |
| Gemini input tokens | N/A | 159,456 | Reported by Gemini usage metadata |
| Gemini output tokens | N/A | 181,886 | Strongly inflated by the contents-page runaway |

## Manual quality findings

### Table of contents: decisive Mistral win

On PDF page 2, Mistral produced a compact three-column Markdown table with section
numbers, titles, and page numbers. Gemini treated every printed dot leader as
mathematics and repeated `\dots` until that single page reached **196,611
characters**. This page accounts for nearly all of the difference in total output
size and a large fraction of Gemini's billed output tokens.

This is a release-blocking failure because it pollutes chunks and embeddings,
increases cost, can crowd useful evidence out of an LLM context window, and may
cause downstream request-size limits to fail.

### Mathematical transcription: Gemini usually cleaner, but neither is perfect

Gemini used compact `pmatrix`, `cases`, `aligned`, and conventional `$...$` /
`$$...$$` notation. Mistral often produced valid but noisy LaTeX with excessive
spaces and `array` environments.

Two sampled numeric errors favored Gemini:

- Page 19 ground truth contains the transition-matrix row
  `(0.5, 0.25, 0.25)`. Gemini reproduced it correctly. Mistral emitted spaced
  decimals such as `0. 2 5`, which changes the rendered expression.
- Page 31 ground truth contains
  `[[1/2, 1/2], [1/4, 3/4]]`. Gemini reproduced it correctly, while Mistral
  changed the upper-right entry to `1/4`.

The matrix errors are especially important for this application because a fluent
but numerically wrong formula can produce incorrect tutoring and evaluation even
when lexical recall remains high.

### Theorem, definition, and proof structure: slight Gemini win

Both converters preserved the sampled theorem/proof order. Gemini more consistently
rendered labels such as `**Theorem 3.5.9.**` and `**Proof.**`, with cleaner cases and
aligned equations. Mistral remained understandable but had noisier formula syntax.

### Figures and plots: decisive Mistral win

Page 43 contains multiple gambler's-ruin plots. Mistral returned Markdown references
and the corresponding image bytes, including five extracted images on that sampled
page. Gemini retained the captions but omitted the actual plots. Elsewhere it emitted
references such as `![...](image)` while returning an empty `images` mapping, so those
references cannot be resolved by the ingestion pipeline.

For a mathematics textbook, losing diagrams and plots is a semantic loss, not only
a display problem.

### Prose and bibliography: close, with cleaner Gemini paragraphs

Both converters accurately handled ordinary prose. On page 132, Gemini separated
bibliography entries into readable paragraphs; Mistral concatenated many entries.
The whole-document lexical metric nevertheless slightly favored Mistral, and the
median results were nearly identical.

## Engineering assessment

### Mistral strengths

- Works with the project's current model configuration.
- One request successfully returned the full 132-page document.
- Faster on this file despite waiting for one large server-side operation.
- Stable output size with no catastrophic repetition.
- Native figure extraction produces usable bytes for downstream storage.
- Slightly higher mean and median selectable-text coverage.

### Mistral weaknesses

- At least two sampled matrices contained material numeric transcription errors.
- LaTeX is noisier and less idiomatic than Gemini's output.
- A single whole-document request has no project-level partial checkpoint if the
  request fails near completion.

### Gemini strengths

- Cleaner, more conventional LaTeX on formula-heavy pages.
- Correct on both manually checked matrices that Mistral mistranscribed.
- Strong prose coverage and readable theorem/proof formatting.
- Page-level architecture could support targeted retry and checkpointing after the
  implementation is extended.

### Gemini weaknesses

- The configured model is retired for this account, so the current code path fails.
- URL-query authentication can expose the API key through exception logging.
- A catastrophic repetition occurred on a common document structure: dotted TOC
  leaders.
- No output-length bound, repetition detector, or page-quality gate stopped it.
- Serial requests made the successful comparison 2.23x slower than Mistral.
- The implementation discards all completed page work if any later page fails.
- Images are not extracted; emitted image placeholders are unresolved.

## Recommendation

Use **Mistral OCR 4 as the default converter for this document and for current
production ingestion**. Gemini 3.5 is promising as a formula verifier or targeted
fallback, but the current Gemini pipeline is not safe as a full-document default.

Recommended follow-up work, in priority order:

1. Replace the retired Gemini model with a supported configurable model and add a
   startup capability check.
2. Move the Gemini key from the query string to `x-goog-api-key`; rotate any key
   that has appeared in logs.
3. Add a hard per-page output-token/character limit, repeated-sequence detection,
   and a prompt rule not to reproduce visual dot leaders.
4. Persist page checkpoints and retry only failed pages. Without checkpoints, the
   current page-by-page design has no recovery advantage.
5. Add bounded concurrency after rate-limit testing to reduce the 610-second serial
   runtime.
6. Either extract and persist figures in the Gemini path or forbid unresolved image
   references.
7. For formula-critical material, use a hybrid quality gate: keep Mistral's document
   structure and images, then send math-dense or suspicious pages to Gemini for
   equation verification. Numeric agreement should be checked before replacing the
   original formula.

## Reproducibility notes

- The Mistral run used `MistralOcrConverter.convert()` without behavioral changes.
- The first Gemini run used `GeminiConverter.convert()` unchanged and failed before
  page 1 due to the retired model.
- The quality-only Gemini run preserved the converter's 2x page rendering, prompt,
  sequential order, joining, and output parsing. It changed only the model to
  `gemini-3.5-flash-lite` and moved the same API key from the URL query parameter to
  the request header.
- No converter source code, `.env`, or PDF content was modified by this evaluation.
- Timings are single end-to-end observations and should not be treated as formal
  latency distributions. A benchmark suite should repeat the run to report p50/p95,
  but repeating a 132-page paid conversion was not necessary to identify the large
  quality differences above.
