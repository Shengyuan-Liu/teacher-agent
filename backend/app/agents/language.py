"""Deciding which language to answer in.

"Answer in the same language as the question" is not reliable on its own — the
model has been observed answering an English question in Japanese. Naming the
language explicitly is far stronger, so resolve it here and put the name in the
prompt.

Guessing wrong is worse than not guessing: a mislabelled language actively
pushes the model away from the right one. Script detection is definitive where
it applies; for Latin scripts, short questions fall back to the generic
instruction rather than a coin flip.
"""

import unicodedata

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0

MIN_LATIN_CHARS = 30
MIN_CONFIDENCE = 0.90

NAMES = {
    "en": "English",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
}


def _scripts(text: str) -> set[str]:
    found = set()
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        for script in ("HIRAGANA", "KATAKANA", "HANGUL", "CJK", "CYRILLIC", "ARABIC", "THAI"):
            if name.startswith(script):
                found.add(script)
                break
    return found


def _by_script(text: str) -> str | None:
    scripts = _scripts(text)
    # Kana settles the Chinese/Japanese question that trips up statistical
    # detectors on short text.
    if scripts & {"HIRAGANA", "KATAKANA"}:
        return "Japanese"
    if "HANGUL" in scripts:
        return "Korean"
    if "CJK" in scripts:
        return "Chinese"
    if "CYRILLIC" in scripts:
        return "Russian"
    if "ARABIC" in scripts:
        return "Arabic"
    if "THAI" in scripts:
        return "Thai"
    return None


def answer_language(question: str) -> str | None:
    """The language to answer in, or None when we should not commit to one."""
    by_script = _by_script(question)
    if by_script:
        return by_script

    text = question.strip()
    if len(text) < MIN_LATIN_CHARS:
        return None
    try:
        best = detect_langs(text)[0]
    except LangDetectException:
        return None
    if best.prob < MIN_CONFIDENCE:
        return None
    return NAMES.get(best.lang)


def instruction(question: str) -> str:
    language = answer_language(question)
    if language:
        return f"Write your entire answer in {language}."
    return "Write your entire answer in the same language as the question."
