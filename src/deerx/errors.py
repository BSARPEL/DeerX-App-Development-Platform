"""DeerX hata hiyerarsisi."""

from __future__ import annotations


class DeerXError(Exception):
    """Tum DeerX hatalarinin kokU."""


class ConfigError(DeerXError):
    """Eksik/gecersiz konfigurasyon."""


class WorkspaceError(DeerXError):
    """Calisma alani disina cikma, izin gibi guvenlik ihlalleri."""


class ToolError(DeerXError):
    """Bir aracin calistirilabilir olmayan bicimde basarisiz olmasi.

    Ajan dongusunde yakalanip modele `is_error=True` tool_result olarak
    donulur; dongu kirilmaz.
    """


class ApprovalDenied(ToolError):
    """Kullanici onay istenen bir islemi reddetti."""


class LLMError(DeerXError):
    """Model cagrisi kurtarilamaz sekilde basarisiz oldu."""


class BudgetExceeded(DeerXError):
    """Token/maliyet butcesi asildi."""


class IngestError(DeerXError):
    """Dokuman okuma/parcalama hatasi."""
