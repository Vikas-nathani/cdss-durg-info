import re
from typing import Optional

# Split on a period that is NOT:
#   - preceded by a digit           → avoids decimals like 5.1, 1.5
#   - followed by a digit           → same
#   - part of a letter abbreviation → avoids U.S., E.U., U.K. where the period
#                                     is the separator between single-letter parts
#
# The last rule is handled in the positive lookahead:
#   [A-Z](?!\.)  — uppercase letter only when NOT itself followed by another period
#                  (i.e. the uppercase is a real sentence start, not "U" in "U.S.")
#   (            — open-paren cross-reference like ( 5.1 )
#   \d+\.\d      — section number like 5.2 (digit.digit, not bare number like "No. 5")
_SENTENCE_END = re.compile(
    r'(?<!\d)'                          # not preceded by digit
    r'\.'                               # literal period
    r'(?!\d)'                           # not followed immediately by digit
    r'[ \t]*'                           # optional spaces/tabs
    r'(?=[A-Z](?!\.)|\(|\d+\.\d)'      # start of real sentence, cross-ref, or section #
)

# ALL-CAPS section title directly abutting a sentence-case sentence with no period.
# e.g. "PRECAUTIONS Thromboembolism" → "PRECAUTIONS\nThromboembolism"
# Requires the title word to be ≥3 uppercase letters and the next word to be Title-case.
_CAPS_TITLE_TO_SENTENCE = re.compile(r'([A-Z]{3,})\s+([A-Z][a-z])')


def split_to_bullets(text: Optional[str]) -> list[str]:
    """
    Split pharmaceutical label text into a list of sentence-level bullets.

    Rules:
    - If the text contains • characters, splits on them as primary delimiters.
    - Otherwise splits on sentence-ending periods, but NOT on decimal numbers like 5.1 or 1.5.
    - Splits on ALL-CAPS section titles that run directly into a sentence (no period).
    - Deduplicates bullets by their first 80 characters.
    - Returns [] for empty / None input.
    """
    if not text or not text.strip():
        return []

    text = re.sub(r'[ \t]+', ' ', text).strip()

    raw_parts = text.split('•') if '•' in text else [text]

    bullets = []
    seen: set[str] = set()

    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        part = _CAPS_TITLE_TO_SENTENCE.sub(r'\1\n\2', part)
        part = _SENTENCE_END.sub('.\n', part)
        for line in part.splitlines():
            line = line.strip()
            if not line or len(line) <= 5:
                continue
            key = line[:80]
            if key in seen:
                continue
            seen.add(key)
            bullets.append(line)

    return bullets
