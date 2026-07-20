"""
artifacts.py

Text-level cleanup applied to the final reassembled output of a document,
after layout-based boilerplate/chrome removal and reassembly. These are
artifacts that only make sense to remove once the text is in its final
reading-order form, rather than at the block level:

  - URLs (http/https and bare "www." links)
  - E-mail addresses
  - Decorative separator lines (rows of repeated dashes/underscores/em
    dashes used as visual dividers, e.g. above a block of footnotes)

This exists specifically to support feeding the extracted text into a
downstream word-embedding/topic-modeling pipeline (e.g. BERTopic), where
URLs and e-mail addresses are pure noise tokens: they inflate vocabulary
size with high-entropy strings that carry no topical signal and are
extremely unlikely to repeat in a way that would let a model learn
anything useful from them.

Design notes:
  - Trailing sentence punctuation (a period, comma, closing parenthesis,
    etc. immediately after a URL with no space, e.g. "...malaise/.") is
    preserved -- only the URL itself is removed, so the surrounding
    sentence still ends correctly.
  - Removal never leaves a URL/e-mail's *domain* behind as a stray
    fragment; the whole match is dropped.
  - After removal, purely cosmetic debris this can leave behind (an empty
    "()" where a parenthetical only contained a link, doubled spaces, a
    now-blank line) is cleaned up on a best-effort basis. Not every
    possible grammatical residue is chased down -- e.g. "at ." where a
    sentence ended with "...available at <url>." may remain -- since for
    a topic-modeling use case a stray period or parenthesis is immaterial
    noise, unlike a full URL which contributes several spurious tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# URLs are often word-wrapped across lines in exported PDFs, typically at
# a hyphen (e.g. "...statement-academic-\nfreedom."), since the underlying
# text is just a long run of non-whitespace characters that the original
# page layout happened to break at that point. The alternation below tries
# a literal "-\n" continuation first at each position so a wrapped URL is
# still matched as a single unit and fully removed, rather than leaving a
# stray word fragment (e.g. "freedom.") behind on its own line.
_URL_RE = re.compile(r"(?:https?://|www\.)(?:-\n|[^\s])+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A line consisting only of repeated dash-like characters (plain hyphens,
# en/em dashes, underscores) is a pure visual divider with zero semantic
# content -- e.g. the rule PDF exports often place above a block of
# footnotes. Requires at least 5 repeats so it doesn't match legitimate
# short punctuation like "--" or a single em dash used mid-sentence.
_SEPARATOR_LINE_RE = re.compile(r"^[\s\-\u2010-\u2015_=]{5,}$")

# Empty bracket/parenthesis pairs (optionally containing only whitespace)
# left behind when their entire contents were a URL/e-mail that got
# removed, e.g. "(see http://example.com)" -> "()".
_EMPTY_BRACKETS_RE = re.compile(r"[\(\[]\s*[\)\]]")

_MULTI_SPACE_RE = re.compile(r" {2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.,;:])")


def _strip_trailing_punct(matched: str) -> tuple:
    """Split a regex match into (core, trailing_punct), where trailing
    punctuation immediately glued to the end (e.g. the closing '.' of a
    sentence with no space before it) is separated out so it can be kept
    in the surrounding text after the core match is removed."""
    trailing = ""
    s = matched
    while s and s[-1] in ".,;:!?'\")]}\u201d\u2019":
        trailing = s[-1] + trailing
        s = s[:-1]
    return s, trailing


def _make_stripper(pattern: "re.Pattern"):
    count = 0

    def _sub(m: "re.Match") -> str:
        nonlocal count
        count += 1
        _core, trailing = _strip_trailing_punct(m.group(0))
        return trailing

    def run(text: str):
        nonlocal count
        count = 0
        new_text = pattern.sub(_sub, text)
        return new_text, count

    return run


@dataclass
class ArtifactCleanupResult:
    text: str
    urls_removed: int = 0
    emails_removed: int = 0
    separator_lines_removed: int = 0


def clean_artifacts(
    text: str,
    remove_urls: bool = True,
    remove_emails: bool = True,
    remove_separator_lines: bool = True,
) -> ArtifactCleanupResult:
    """
    Apply URL/e-mail/decorative-separator cleanup to a final block of
    extracted text. Each cleanup step is independently toggleable so
    callers (e.g. the CLI) can opt out.
    """
    urls_removed = 0
    emails_removed = 0
    separator_lines_removed = 0

    if remove_urls:
        text, urls_removed = _make_stripper(_URL_RE)(text)
    if remove_emails:
        text, emails_removed = _make_stripper(_EMAIL_RE)(text)

    if remove_separator_lines:
        lines = text.split("\n")
        kept_lines = []
        for line in lines:
            if _SEPARATOR_LINE_RE.match(line):
                separator_lines_removed += 1
                continue
            kept_lines.append(line)
        text = "\n".join(kept_lines)

    if remove_urls or remove_emails:
        # Best-effort cosmetic cleanup of debris left by removing a link
        # that was the entire content of a parenthetical, plus any double
        # spacing introduced by the removal.
        text = _EMPTY_BRACKETS_RE.sub("", text)
        text = _MULTI_SPACE_RE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
        # Drop lines that are now empty or contain only leftover
        # punctuation (e.g. a footnote line that was purely a URL).
        lines = [
            line
            for line in text.split("\n")
            if line.strip(" .,;:()[]\u201c\u201d\u2018\u2019")
        ]
        text = "\n".join(lines)

    return ArtifactCleanupResult(
        text=text,
        urls_removed=urls_removed,
        emails_removed=emails_removed,
        separator_lines_removed=separator_lines_removed,
    )
