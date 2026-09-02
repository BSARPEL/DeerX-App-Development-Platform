"""Yapi farkindali parcalama.

Amac: her parcanin tek basina okundugunda anlamli olmasi. Bunun icin
    * markdown/dokuman metni baslik hiyerarsisine gore bolunur,
    * kod dosyalari ust duzey tanim sinirlarindan bolunur,
    * her parca `heading_path` ile baglamlandirilir (or. "Mimari > Veri Katmani").

Token sayimi yaklasiktir; amac kesin fatura degil, tutarli pencere boyutudur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Turkce/Ingilizce karisik metin icin gozlemlenen ortalama.
CHARS_PER_TOKEN = 3.6

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*```")
_TOP_LEVEL_DEF_RE = re.compile(
    r"^(?:"
    r"(?:async\s+)?def\s+\w+|"                       # python
    r"class\s+\w+|"                                   # python/ts/java
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+|"  # js/ts
    r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(|"  # js/ts arrow
    r"(?:export\s+)?(?:interface|type|enum)\s+\w+|"   # ts
    r"func\s+(?:\([^)]*\)\s*)?\w+|"                    # go
    r"(?:pub\s+)?(?:async\s+)?fn\s+\w+|"               # rust
    r"impl(?:<[^>]*>)?\s+\w+|"                          # rust
    r"(?:public|private|protected|internal)\s+[\w<>\[\],\s]+\s+\w+\s*\("  # java/c#
    r")"
)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


@dataclass(slots=True)
class Chunk:
    """Bilgi tabanina yazilan en kucuk birim."""

    text: str
    ordinal: int
    heading_path: str = ""
    start_line: int = 1
    end_line: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def contextualized(self) -> str:
        """Gomme icin baslik yolunu metnin basina ekler."""
        if not self.heading_path:
            return self.text
        return f"{self.heading_path}\n\n{self.text}"


@dataclass(slots=True)
class _Section:
    heading_path: str
    lines: list[tuple[int, str]]  # (satir_no, icerik)


def _split_markdown_sections(text: str) -> list[_Section]:
    """Metni baslik hiyerarsisine gore bolumlere ayirir."""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (seviye, baslik)
    current = _Section(heading_path="", lines=[])
    in_fence = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence

        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            if current.lines:
                sections.append(current)
            level, title = len(match.group(1)), match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = " > ".join(t for _, t in stack)
            current = _Section(heading_path=path, lines=[(lineno, line)])
        else:
            current.lines.append((lineno, line))

    if current.lines:
        sections.append(current)
    return [s for s in sections if any(line.strip() for _, line in s.lines)]


def _split_code_sections(text: str) -> list[_Section]:
    """Kodu ust duzey tanim sinirlarindan bolumlere ayirir."""
    sections: list[_Section] = []
    current = _Section(heading_path="", lines=[])
    last_symbol = ""

    for lineno, line in enumerate(text.splitlines(), start=1):
        # Yalnizca girintisiz (ust duzey) tanimlar bolum sinirlari olur.
        is_boundary = bool(line) and not line[0].isspace() and _TOP_LEVEL_DEF_RE.match(line.strip())
        if is_boundary and current.lines:
            sections.append(current)
            current = _Section(heading_path=last_symbol, lines=[])
        if is_boundary:
            last_symbol = line.strip().rstrip(":{ ")[:120]
            current.heading_path = last_symbol
        current.lines.append((lineno, line))

    if current.lines:
        sections.append(current)
    return [s for s in sections if any(line.strip() for _, line in s.lines)]


def _window_section(
    section: _Section,
    max_tokens: int,
    overlap_tokens: int,
    ordinal_start: int,
) -> list[Chunk]:
    """Bir bolumu, gerekiyorsa ortusmeli pencerelere boler."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    overlap_chars = int(overlap_tokens * CHARS_PER_TOKEN)

    body = "\n".join(line for _, line in section.lines).strip("\n")
    if not body.strip():
        return []

    start_line = section.lines[0][0]
    end_line = section.lines[-1][0]

    if len(body) <= max_chars:
        return [
            Chunk(
                text=body,
                ordinal=ordinal_start,
                heading_path=section.heading_path,
                start_line=start_line,
                end_line=end_line,
            )
        ]

    chunks: list[Chunk] = []
    lines = section.lines
    buf: list[tuple[int, str]] = []
    size = 0
    ordinal = ordinal_start

    def flush() -> None:
        nonlocal buf, size, ordinal
        if not buf:
            return
        chunk_text = "\n".join(line for _, line in buf).strip("\n")
        if chunk_text.strip():
            chunks.append(
                Chunk(
                    text=chunk_text,
                    ordinal=ordinal,
                    heading_path=section.heading_path,
                    start_line=buf[0][0],
                    end_line=buf[-1][0],
                    meta={"windowed": True},
                )
            )
            ordinal += 1
        # Ortusme: sondan overlap_chars kadarini bir sonraki pencereye tasi.
        carry: list[tuple[int, str]] = []
        carried = 0
        for item in reversed(buf):
            carried += len(item[1]) + 1
            carry.append(item)
            if carried >= overlap_chars:
                break
        buf = list(reversed(carry))
        size = sum(len(line) + 1 for _, line in buf)

    for item in lines:
        buf.append(item)
        size += len(item[1]) + 1
        if size >= max_chars:
            flush()
    if buf and (not chunks or size > overlap_chars):
        # Kalan icerigi yaz; yalnizca ortusme artigi kalmissa atla.
        chunk_text = "\n".join(line for _, line in buf).strip("\n")
        if chunk_text.strip():
            chunks.append(
                Chunk(
                    text=chunk_text,
                    ordinal=ordinal,
                    heading_path=section.heading_path,
                    start_line=buf[0][0],
                    end_line=buf[-1][0],
                    meta={"windowed": True},
                )
            )
    return chunks


def chunk_text(
    text: str,
    *,
    kind: str = "doc",
    max_tokens: int = 700,
    overlap_tokens: int = 100,
    min_tokens: int = 24,
) -> list[Chunk]:
    """Metni parcalara ayirir.

    Cok kucuk bolumler bir sonrakiyle birlestirilir; boylece "Giris" gibi tek
    cumlelik basliklar tek basina parca olmaz.
    """
    sections = _split_code_sections(text) if kind == "code" else _split_markdown_sections(text)
    if not sections:
        return []

    # Kucuk komsu bolumleri birlestir.
    merged: list[_Section] = []
    for section in sections:
        body = "\n".join(line for _, line in section.lines)
        if (
            merged
            and estimate_tokens(body) < min_tokens
            and estimate_tokens("\n".join(line for _, line in merged[-1].lines)) < max_tokens
        ):
            merged[-1].lines.extend(section.lines)
            if section.heading_path and not merged[-1].heading_path:
                merged[-1].heading_path = section.heading_path
        else:
            merged.append(section)

    chunks: list[Chunk] = []
    for section in merged:
        produced = _window_section(section, max_tokens, overlap_tokens, len(chunks))
        chunks.extend(produced)

    # Sirali numaralandirmayi garanti et.
    for index, chunk in enumerate(chunks):
        chunk.ordinal = index
    return chunks
