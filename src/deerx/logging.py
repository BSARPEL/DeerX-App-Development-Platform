"""Rich tabanli konsol ve olay gunlugu.

Ajanlarin urettigi her adim hem terminale hem de calisma dizinindeki
`.deerx/events.jsonl` dosyasina yazilir; boylece kosu sonradan denetlenebilir.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

_THEME = Theme(
    {
        "phase": "bold cyan",
        "agent": "bold magenta",
        "tool": "yellow",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "dim": "dim",
        "cost": "bold blue",
    }
)


def _use_utf8_streams() -> None:
    """stdout/stderr'i UTF-8'e cevirir.

    Windows konsolu varsayilan olarak yerel kod sayfasini kullanir (or. cp1254);
    Unicode bir simge yazmak `UnicodeEncodeError` ile sureci dusurur. `errors`
    degeri de gevsetilir ki cevrilemeyen bir karakter cikti almayi engellemesin.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - kapali/yonlendirilmis akis
            pass


_use_utf8_streams()

console = Console(theme=_THEME, soft_wrap=False)


def _supports(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


# UTF-8'e gecilemeyen bir terminalde (or. yonlendirilmis cikti, eski konsol)
# ASCII karsiliklarina duseriz; simge yuzunden cikti kaybolmamali.
_RICH_GLYPHS = {
    "phase": "▸", "agent": "◆", "tool": "→", "tool_error": "✗",
    "error": "✗", "warn": "!", "done": "✓", "cost": "$",
    "approval": "?", "needs_input": "?", "message": "»", "default": "·",
}
_ASCII_GLYPHS = {
    "phase": ">", "agent": "*", "tool": "->", "tool_error": "x",
    "error": "x", "warn": "!", "done": "+", "cost": "$",
    "approval": "?", "needs_input": "?", "message": ">>", "default": "-",
}
GLYPHS = _RICH_GLYPHS if _supports("".join(_RICH_GLYPHS.values())) else _ASCII_GLYPHS


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    # Kutuphane gurultusunu kis
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "fastembed"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"deerx.{name}")


@dataclass(slots=True)
class Event:
    """Denetlenebilir tek bir kosu olayi."""

    kind: str
    actor: str
    message: str
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    # Olayin uretildigi faz. Arayuzun kosuyu adim adim gosterebilmesi
    # icin gerekli; olaylari sonradan aktor adindan fazlara ayirmak
    # kirilgandir (ayni rol birden fazla fazda kosabilir).
    phase: str | None = None
    # Olayin ait oldugu kosu. Her kosu kendi kimligiyle saklanir ve
    # gecmise donuk incelenir; faz durumu tekrar kosuda uzerine yazilir.
    run_id: str | None = None


class EventLog:
    """JSONL olay gunlugu. Ayni anda konsola da yazar."""

    # Olay gunlugu bu boyutu asinca `.1` uzantisiyla arsivlenir. Uzun kosular
    # aksi halde diski sisirir; tek bir yedek tutmak son kosuyu incelemeye yeter.
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, path: Path | None = None, echo: bool = True) -> None:
        self.path = path
        self.echo = echo
        self._listeners: list[Any] = []
        # Orkestrator faza girerken bunu ayarlar; her olay o faza
        # etiketlenir ve arayuz kosuyu adim adim gosterebilir.
        self.current_phase: str | None = None
        self.current_run: str | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_large()

    def _rotate_if_large(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            if self.path.stat().st_size < self.MAX_BYTES:
                return
            backup = self.path.with_suffix(self.path.suffix + ".1")
            backup.unlink(missing_ok=True)
            self.path.rename(backup)
        except OSError:  # pragma: no cover - gunluk donusumu kosuyu durdurmaz
            pass

    def subscribe(self, fn: Any) -> None:
        self._listeners.append(fn)

    def emit(self, kind: str, actor: str, message: str, **data: Any) -> Event:
        ev = Event(
            kind=kind, actor=actor, message=message, data=data,
            phase=self.current_phase, run_id=self.current_run,
        )
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(ev), ensure_ascii=False, default=str) + "\n")
        if self.echo:
            self._print(ev)
        for fn in self._listeners:
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 - dinleyici hatasi kosuyu durdurmasin
                pass
        return ev

    @staticmethod
    def _print(ev: Event) -> None:
        style = {
            "phase": "phase",
            "agent": "agent",
            "tool": "tool",
            "tool_error": "err",
            "error": "err",
            "warn": "warn",
            "done": "ok",
            "cost": "cost",
            "approval": "warn",
            "needs_input": "warn",
            "message": "dim",
        }.get(ev.kind, "dim")
        prefix = GLYPHS.get(ev.kind, GLYPHS["default"])
        # Olay metni model/arac ciktisindan gelir; rich biciminde koseli parantez
        # icerebilir. `markup=False` bunu yazi olarak birakir, bicimlendirme
        # hatasina donusturmez.
        try:
            console.print(f"[{style}]{prefix} {ev.actor}[/{style}] ", end="")
            console.print(ev.message, markup=False, highlight=False)
        except UnicodeEncodeError:
            # stdout baska bir katman tarafindan sarmalanmis ve UTF-8 kabul
            # etmiyor olabilir (test kosucusu, eski konsol, yonlendirme).
            # Bir olay yazamamak kosuyu dusurmemeli.
            ascii_prefix = _ASCII_GLYPHS.get(ev.kind, _ASCII_GLYPHS["default"])
            safe = ev.message.encode("ascii", "replace").decode("ascii")
            try:
                console.print(f"[{style}]{ascii_prefix} {ev.actor}[/{style}] ", end="")
                console.print(safe, markup=False, highlight=False)
            except Exception:  # noqa: BLE001 - gunluk yazamamak kosuyu durdurmaz
                pass


NULL_LOG = EventLog(path=None, echo=False)
