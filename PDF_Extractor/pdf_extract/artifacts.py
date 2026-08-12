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
  - Typographic ligature codepoints (e.g. U+FB01 "ﬁ") normalized back to
    their constituent letters (e.g. "fi")
  - Private-use-area / symbol-font glyphs left behind by icon fonts
    (e.g. social-share icons rendered as text in some web-article PDF
    exports), which extract as meaningless codepoints with no
    corresponding real character

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

# Standard Latin typographic ligatures. PDF fonts commonly encode common
# letter pairs (fi, fl, ff, ffi, ffl) as a single glyph/codepoint for
# visual kerning reasons; when extracted as text this single codepoint
# reads as one (unusual, non-ASCII) character rather than the two/three
# letters it visually represents. Expanding these back to plain letters
# is a standard, universal (genre- and language-independent within
# Latin-script text) normalization for any downstream text/NLP use --
# leaving them as-is would inflate vocabulary with visually-identical-but-
# distinct tokens (e.g. "ﬁred" vs "fired" would be treated as different
# words by a topic model).
_LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",  # long-s + t ligature, rare but seen in some fonts
    "\ufb06": "st",
}
_LIGATURE_RE = re.compile("[" + "".join(_LIGATURE_MAP.keys()) + "]")

# Icon/symbol-font glyphs (e.g. a site's social-share icon font, star
# ratings, arrow glyphs) are sometimes embedded as regular text runs in a
# PDF export, so they get extracted as ordinary text -- but the resulting
# codepoints are meaningless outside that specific icon font (they render
# as a Facebook logo, a share arrow, etc. only when displayed with that
# font; as plain text they're just noise, often landing in Unicode's
# Private Use Area or in symbol/dingbat ranges). These are stripped
# outright rather than mapped to anything, since there is no textual
# equivalent to substitute.
_ICON_GLYPH_RE = re.compile(
    r"[\uE000-\uF8FF\U000F0000-\U000FFFFD\u2600-\u27BF\U0001F000-\U0001FFFF]"
)

# Raw HTML tag leakage, e.g. "<a href=" http://example.com" target=_blank">".
# Observed in a real web-article PDF export where the underlying page's
# raw anchor-tag markup (rather than just its rendered/visible link text)
# got captured as literal text -- likely a copy-paste-from-browser or a
# broken PDF-export step that didn't fully strip markup before laying out
# the page. This is pure markup noise for a downstream embedding/topic-
# modeling pipeline, exactly like a URL, so it's removed the same way.
#
# Matched generically as "< ... >" spanning a tag name plus optional
# attributes, rather than an exact literal string, so it generalizes to
# any HTML tag that leaks through this way (a link, a span, a div, etc.),
# not just this one specific anchor tag. Deliberately does NOT match a
# bare "<" or ">" used as a real comparison/inequality symbol in body
# text (e.g. "x < 5"), since it requires a tag-name-like word
# immediately after "<" with no intervening space, and requires the "<"
# and ">" to be reasonably close together (an angle bracket used as
# math notation would essentially never have a word-like token followed
# eventually by a lone ">" within a short span in real prose). The
# pattern also tolerates the mismatched/unescaped quote characters seen
# in the real observed case (the source markup's quotes appear to have
# been mangled by the PDF's own text encoding), where a well-formed-
# HTML-only parser would fail to match.
_HTML_TAG_RE = re.compile(r"<\s*/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]{0,200})?>")

_MULTI_SPACE_RE = re.compile(r" {2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.,;:])")

# Dangling link/address "introducer" phrases left behind at the end of a
# line when the URL/e-mail that followed sat on its own separate line
# (common in letterheads and footnote blocks, e.g. "...Canada; e-mail: \n"
# followed by "safs@safs.ca" on the next line -- removing the address
# leaves "...Canada; e-mail:" dangling with nothing after it).
#
# This list is deliberately narrow and specific rather than a general
# "line ends with a short word" heuristic: real body text legitimately
# word-wraps mid-sentence at arbitrary points, including onto words like
# "at", "see", or "via" (e.g. "...it would have taken no time at" wrapping
# onto "all..." on the next line) -- so a generic "strip trailing short
# word" rule would corrupt real sentences. Each phrase below is instead
# a specific, unambiguous link/contact-info introducer that essentially
# never occurs as the natural end of a wrapped sentence for any other
# reason, based on patterns observed across a real batch of letters and
# reports. Only the introducer phrase itself (plus any immediately
# preceding "," or ";" separator) is removed; the rest of the line -- the
# actual content before it -- is preserved.
_DANGLING_LINK_INTRODUCER_RE = re.compile(
    r"[,;]?[ \t]*\b(?:e-?mail|facebook|twitter|linkedin|instagram|website)\s*:[ \t]*$"
    r"|\bwebsite\s+at[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


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
    ligatures_normalized: int = 0
    icon_glyphs_removed: int = 0
    html_tags_removed: int = 0


def _normalize_ligatures(text: str):
    count = 0

    def _sub(m: "re.Match") -> str:
        nonlocal count
        count += 1
        return _LIGATURE_MAP[m.group(0)]

    new_text = _LIGATURE_RE.sub(_sub, text)
    return new_text, count


def clean_artifacts(
    text: str,
    remove_urls: bool = True,
    remove_emails: bool = True,
    remove_separator_lines: bool = True,
    normalize_ligatures: bool = True,
    remove_icon_glyphs: bool = True,
    remove_html_tags: bool = True,
) -> ArtifactCleanupResult:
    """
    Apply URL/e-mail/decorative-separator/ligature/icon-glyph/HTML-tag
    cleanup to a final block of extracted text. Each cleanup step is
    independently toggleable so callers (e.g. the CLI) can opt out.
    """
    urls_removed = 0
    emails_removed = 0
    separator_lines_removed = 0
    ligatures_normalized = 0
    icon_glyphs_removed = 0
    html_tags_removed = 0

    if normalize_ligatures:
        text, ligatures_normalized = _normalize_ligatures(text)

    if remove_icon_glyphs:
        icon_glyphs_removed = len(_ICON_GLYPH_RE.findall(text))
        text = _ICON_GLYPH_RE.sub("", text)

    if remove_html_tags:
        html_tags_removed = len(_HTML_TAG_RE.findall(text))
        text = _HTML_TAG_RE.sub("", text)

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

    if remove_urls or remove_emails or remove_icon_glyphs or remove_html_tags:
        # Best-effort cosmetic cleanup of debris left by removing a link
        # that was the entire content of a parenthetical, plus any double
        # spacing introduced by the removal.
        text = _EMPTY_BRACKETS_RE.sub("", text)
        # Remove a link/contact introducer phrase left dangling at the end
        # of a line (see _DANGLING_LINK_INTRODUCER_RE docstring above).
        # Only run this when a URL/e-mail was actually removed by this
        # call -- it's specifically cleanup for debris *this cleanup step*
        # can cause, not a general-purpose text transformation.
        if urls_removed or emails_removed:
            text = _DANGLING_LINK_INTRODUCER_RE.sub("", text)
        text = _MULTI_SPACE_RE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
        # Drop lines that are now empty or contain only leftover
        # punctuation/list-marker characters (e.g. a footnote line that
        # was purely a URL, or a bullet/dash list marker left orphaned
        # after its "Email:"/"Website:" label was cleaned up above, or a
        # line that was purely icon-font glyphs).
        lines = [
            line
            for line in text.split("\n")
            if line.strip(" .,;:()[]\u201c\u201d\u2018\u2019\u2022\u25cf\u25e6-")
        ]
        text = "\n".join(lines)

    return ArtifactCleanupResult(
        text=text,
        urls_removed=urls_removed,
        emails_removed=emails_removed,
        separator_lines_removed=separator_lines_removed,
        ligatures_normalized=ligatures_normalized,
        icon_glyphs_removed=icon_glyphs_removed,
        html_tags_removed=html_tags_removed,
    )
