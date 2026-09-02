"""Dosya sistemi araclari. Tum yollar calisma alanina hapsedilmistir."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult

_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".deerx", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", "target",
}
_MAX_READ_BYTES = 400_000


class ReadFile(Tool):
    name = "read_file"
    description = """
    Calisma alanindaki bir dosyayi okur. Buyuk dosyalarda `offset` ve `limit`
    (satir bazli) kullanin. Cikti, duzenleme yaparken hizalamayi kolaylastirmak
    icin satir numaralidir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Calisma alanina gore dosya yolu."},
            "offset": {"type": "integer", "description": "Baslangic satiri (1 tabanli)."},
            "limit": {"type": "integer", "description": "Okunacak satir sayisi (varsayilan 800)."},
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str, offset: int = 1, limit: int = 800) -> ToolResult:
        target = ctx.resolve_path(path, must_exist=True)
        if target.is_dir():
            raise ToolError(t("fs.is_a_dir", path=path))
        if target.stat().st_size > _MAX_READ_BYTES:
            raise ToolError(t("fs.too_large", path=path, size=f"{target.stat().st_size:,}"))

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = target.read_text(encoding="latin-1")
            except Exception as exc:  # noqa: BLE001
                raise ToolError(t("fs.not_text", path=path, error=exc)) from exc

        lines = text.splitlines()
        start = max(1, offset)
        end = min(len(lines), start + max(1, limit) - 1)
        body = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
        note = ""
        if end < len(lines):
            note = f"\n\n[{len(lines) - end} satir daha var; offset={end + 1} ile devam edin]"
        return ToolResult(content=f"{ctx.relative(target)} ({len(lines)} satir)\n\n{body}{note}")


class WriteFile(Tool):
    name = "write_file"
    description = """
    Dosyayi verilen icerikle tamamen yazar (varsa uzerine yazar, yoksa olusturur).
    Ust dizinler otomatik olusturulur. Kismi degisiklikler icin `edit_file` tercih edin.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Calisma alanina gore dosya yolu."},
            "content": {"type": "string", "description": "Dosyanin tam yeni icerigi."},
        },
        "required": ["path", "content"],
    }

    def run(self, ctx: ToolContext, path: str, content: str) -> ToolResult:
        target = ctx.resolve_path(path)
        existed = target.exists()
        preview = content if len(content) < 1500 else content[:1500] + "\n…"
        ctx.approve(
            f"{'Uzerine yaz' if existed else 'Olustur'}: {ctx.relative(target)}",
            preview,
            signature=f"write:{ctx.relative(target)}",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        ctx.events.emit(
            "tool", "fs",
            f"{t('fs.updated_event') if existed else t('fs.created_event')}: "
            f"{ctx.relative(target)}",
        )
        return ToolResult(
            content=t(
                "fs.written",
                path=ctx.relative(target),
                lines=len(content.splitlines()),
            ),
            data={"path": str(target), "created": not existed},
        )


class EditFile(Tool):
    name = "edit_file"
    description = """
    Dosyada tam metin degisimi yapar. `old_string` dosyada BIRE BIR ve TEKIL
    olmalidir; degilse hata doner. Cevresine yeterli baglam ekleyerek tekil hale
    getirin. `replace_all` ile tum eslesmeler degistirilir.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Degistirilecek tam metin."},
            "new_string": {"type": "string", "description": "Yerine yazilacak metin."},
            "replace_all": {"type": "boolean", "description": "Tum eslesmeleri degistir."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(
        self,
        ctx: ToolContext,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        target = ctx.resolve_path(path, must_exist=True)
        text = target.read_text(encoding="utf-8")

        count = text.count(old_string)
        if count == 0:
            raise ToolError(t("fs.not_found_in_file", path=ctx.relative(target)))
        if count > 1 and not replace_all:
            raise ToolError(t("fs.not_unique", count=count))

        ctx.approve(
            f"Duzenle: {ctx.relative(target)}",
            f"- {old_string[:600]}\n+ {new_string[:600]}",
            signature=f"write:{ctx.relative(target)}",
        )
        updated = text.replace(old_string, new_string) if replace_all else text.replace(
            old_string, new_string, 1
        )
        target.write_text(updated, encoding="utf-8")
        ctx.events.emit(
            "tool", "fs",
            t("fs.edited_event", path=ctx.relative(target), count=count),
        )
        return ToolResult(
            content=t("fs.updated", path=ctx.relative(target), count=count)
        )


class ListDir(Tool):
    name = "list_dir"
    description = "Bir dizinin icerigini listeler. Yaygin derleme/bagimlilik dizinleri atlanir."
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Dizin yolu (varsayilan: kok)."},
            "depth": {"type": "integer", "description": "Ic ice inme derinligi (varsayilan 2)."},
        },
    }

    def run(self, ctx: ToolContext, path: str = ".", depth: int = 2) -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        if not root.is_dir():
            raise ToolError(t("fs.not_a_dir", path=path))

        lines: list[str] = []
        limit = 600

        def walk(directory: Path, level: int, prefix: str) -> None:
            if level > depth or len(lines) >= limit:
                return
            try:
                entries = sorted(
                    directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
                )
            except PermissionError:
                return
            for entry in entries:
                if len(lines) >= limit:
                    lines.append("…[kesildi]")
                    return
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    walk(entry, level + 1, prefix + "  ")
                else:
                    size = entry.stat().st_size
                    lines.append(f"{prefix}{entry.name}  ({size:,}b)")

        walk(root, 1, "")
        body = "\n".join(lines) if lines else "(bos)"
        return ToolResult(content=f"{ctx.relative(root)}/\n{body}")


class GlobFiles(Tool):
    name = "glob_files"
    description = "Glob deseniyle dosya arar (or. `src/**/*.py`). Yol listesi doner."
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob deseni."},
            "path": {"type": "string", "description": "Arama koku (varsayilan: calisma alani)."},
        },
        "required": ["pattern"],
    }

    def run(self, ctx: ToolContext, pattern: str, path: str = ".") -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        matches = [
            p for p in root.glob(pattern)
            if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts)
        ]
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            return ToolResult(content=t("fs.no_glob_match", pattern=pattern))
        listing = "\n".join(ctx.relative(p) for p in matches[:300])
        extra = (
            t("fs.more_files", count=len(matches) - 300) if len(matches) > 300 else ""
        )
        return ToolResult(content=listing + extra, data={"count": len(matches)})


class GrepFiles(Tool):
    name = "grep_files"
    description = """
    Dosya iceriklerinde duzenli ifade arar. `glob` ile dosya tipini daraltin.
    Eslesen satirlari dosya:satir bicimiyle doner.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex deseni."},
            "path": {"type": "string", "description": "Arama koku."},
            "glob": {"type": "string", "description": "Dosya filtresi, or. `*.py`."},
            "ignore_case": {"type": "boolean"},
            "max_results": {"type": "integer", "description": "Varsayilan 120."},
        },
        "required": ["pattern"],
    }

    def run(
        self,
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
        glob: str = "*",
        ignore_case: bool = False,
        max_results: int = 120,
    ) -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(t("fs.bad_regex", error=exc)) from exc

        hits: list[str] = []
        scanned = 0
        for file_path in root.rglob("*"):
            if len(hits) >= max_results:
                break
            if not file_path.is_file() or any(part in _SKIP_DIRS for part in file_path.parts):
                continue
            if not fnmatch.fnmatch(file_path.name, glob):
                continue
            if file_path.stat().st_size > _MAX_READ_BYTES:
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            for lineno, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{ctx.relative(file_path)}:{lineno}: {line.strip()[:220]}")
                    if len(hits) >= max_results:
                        break

        if not hits:
            return ToolResult(content=t("fs.no_grep_match", scanned=scanned))
        return ToolResult(
            content=t("fs.grep_hits", count=len(hits), scanned=scanned)
            + "\n" + "\n".join(hits),
            data={"count": len(hits)},
        )


FILESYSTEM_TOOLS: list[Tool] = [
    ReadFile(),
    WriteFile(),
    EditFile(),
    ListDir(),
    GlobFiles(),
    GrepFiles(),
]
