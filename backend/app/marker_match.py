from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_marker_text(value: str) -> str:
    """Normalize text for tolerant probe-marker matching.

    A marker verifies that an instruction was followed, not exact bytes.
    Models legitimately vary letter case (``<!doctype html>``), full-width
    punctuation (``ＲＥＳＵＬＴ＝５３``) and spacing (``RESULT = 53``); none of
    those differences is evidence of degradation, so they must not produce a
    ``marker_miss`` hard signal.
    """

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub("", normalized).casefold()


def expected_marker_matched(expected: str, text: str) -> bool:
    """Return True when the marker appears in the reply or no marker is set."""

    marker = normalize_marker_text(expected)
    if not marker:
        return True
    return marker in normalize_marker_text(text)
