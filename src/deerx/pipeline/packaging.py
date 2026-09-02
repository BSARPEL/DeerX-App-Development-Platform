"""Teslimat paketleme — uretilen projeyi zip olarak toplar.

Boru hattinin son halkasi: kod yazildi, testler kosuldu, inceleme yapildi.
Bu modul isin *teslim edilebilir* halini uretir.

Iki sorumluluk var:
    1. Hazirlik denetimi — paketlemeye deger mi? Basarisiz gorev, kapanmamis
       kritik bosluk veya kosulmamis QA varken "tamam" demek yaniltici olur.
    2. Toplama — hangi dosyalar girecek, hangileri KESINLIKLE girmeyecek.

Sir sizintisi bu modulun en ciddi riskidir: `.env`, anahtar dosyalari ve kimlik
bilgileri hicbir kosulda pakete girmez.
"""

from __future__ import annotations

import fnmatch
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..i18n import t
from ..logging import get_logger
from .models import Artifact, Phase, Severity, Status
from .state import ProjectState

log = get_logger("packaging")

# Paketin kapak sayfasi. Arayuz zip'i acmadan bu dosyayi okuyup gosterir.
MANIFEST_NAME = "TESLIMAT.md"

# Pakete ASLA girmeyecek desenler. Sir sizdirmak, eksik paket vermekten
# kat kat kotudur; bu liste bilerek genis tutulmustur.
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.jks",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
    "credentials", "credentials.*", "*secret*.json", "*secrets*.yml",
    "*.keystore", "service-account*.json",
)

# Uretilen degil, uretim sirasinda olusan dizinler. Yol *parcasi* olarak
# eslesirler: bir monorepo'da `node_modules` koke degil `frontend/node_modules`
# altina duser, bu yuzden yalnizca koke bakan bir glob ("node_modules/*") ise
# yaramaz — binlerce bagimlilik dosyasi pakete sizardi.
NOISE_DIRS = (
    ".git", ".hg", ".svn", ".deerx", ".venv", "venv",
    "node_modules", "__pycache__", "dist", "build", "*.egg-info",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".next", ".nuxt", ".turbo",
)

# `.git` bir dosya da olabilir (alt modullerde "gitdir: ..." satiri tutar).
NOISE_FILES = (
    "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db", "*.log", "*.tmp", ".git",
)

# `.env.example` gibi ornek dosyalar sirsizdir ve kuruluma yardim eder.
SECRET_EXCEPTIONS = (".env.example", ".env.sample", ".env.template")

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024


@dataclass(slots=True)
class ReadinessIssue:
    """Paketlemeyi engelleyen ya da uyarilmasi gereken bir durum."""

    kind: str  # blocker | warning
    message: str

    @property
    def blocking(self) -> bool:
        return self.kind == "blocker"


@dataclass(slots=True)
class Readiness:
    """Projenin teslim edilmeye hazir olup olmadigi."""

    issues: list[ReadinessIssue] = field(default_factory=list)

    @property
    def blockers(self) -> list[ReadinessIssue]:
        return [i for i in self.issues if i.blocking]

    @property
    def warnings(self) -> list[ReadinessIssue]:
        return [i for i in self.issues if not i.blocking]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def report(self) -> str:
        if self.ok and not self.warnings:
            return "Proje teslime hazir: engel ve uyari yok."
        lines: list[str] = []
        if self.blockers:
            lines.append("Paketleme engelleri:")
            lines += [f"  - {i.message}" for i in self.blockers]
        if self.warnings:
            lines.append("Uyarilar:")
            lines += [f"  - {i.message}" for i in self.warnings]
        return "\n".join(lines)


def check_readiness(state: ProjectState) -> Readiness:
    """Projenin teslim edilebilir olup olmadigini denetler.

    Engel (blocker) sayilanlar isin *dogrulanmamis* oldugunu gosterir; uyarilar
    ise eksik ama teslimi anlamsiz kilmayan durumlardir.
    """
    issues: list[ReadinessIssue] = []
    tasks = state.list_tasks()

    if not tasks:
        issues.append(ReadinessIssue("blocker", t("package.plan_empty")))

    failed = [t.key for t in tasks if t.status == Status.FAILED]
    if failed:
        issues.append(
            ReadinessIssue(
                "blocker", t("package.failed_tasks", keys=", ".join(failed))
            )
        )

    unfinished = [
        t.key for t in tasks if t.status in {Status.PENDING, Status.RUNNING, Status.BLOCKED}
    ]
    if unfinished:
        issues.append(
            ReadinessIssue(
                "blocker",
                t(
                    "package.unfinished_tasks",
                    count=len(unfinished),
                    keys=", ".join(unfinished[:8]),
                    more=" …" if len(unfinished) > 8 else "",
                ),
            )
        )

    critical = [
        g.key for g in state.list_gaps()
        if g.severity in {Severity.CRITICAL, Severity.HIGH} and g.status != Status.DONE
    ]
    if critical:
        issues.append(
            ReadinessIssue(
                "warning",
                t(
                    "package.open_gaps",
                    count=len(critical),
                    keys=", ".join(critical[:8]),
                    more=" …" if len(critical) > 8 else "",
                ),
            )
        )

    blocking_questions = state.open_blocking_questions()
    if blocking_questions:
        issues.append(
            ReadinessIssue(
                "blocker",
                t(
                    "package.blocking_questions",
                    keys=", ".join(q.key for q in blocking_questions),
                ),
            )
        )

    # Dogrulama fazlari kosuldu mu? Faz adi `Phase.label`den gelir; burada
    # sabit yazilsa Ingilizce ekranda "Kod incelemesi" diye gorunurdu.
    for phase in (Phase.QA, Phase.REVIEW):
        status = state.phase_status(phase).status
        if status != Status.DONE:
            issues.append(
                ReadinessIssue(
                    "warning",
                    t("package.phase_not_run", label=phase.label, status=status),
                )
            )

    return Readiness(issues=issues)


def _is_secret(relative: str, name: str) -> bool:
    if name in SECRET_EXCEPTIONS:
        return False
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern)
        for pattern in SECRET_PATTERNS
    )


def _is_noise(relative: str, name: str) -> bool:
    """Dosya uretim artigi mi?

    Dizin desenleri yolun *her* parcasina uygulanir; `apps/web/node_modules/...`
    da `node_modules` kadar artiktir.
    """
    parts = relative.split("/")[:-1]
    if any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in NOISE_DIRS):
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in NOISE_FILES)


@dataclass(slots=True)
class PackageResult:
    path: Path
    file_count: int
    total_bytes: int
    excluded_secrets: list[str]
    skipped_large: list[str]
    readiness: Readiness
    # Pakete konan TESLIMAT.md'nin metni; arayuz zip'i acmadan raporu gosterir.
    manifest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "excluded_secrets": self.excluded_secrets,
            "skipped_large": self.skipped_large,
            "ready": self.readiness.ok,
        }


def collect_files(workspace: Path) -> tuple[list[Path], list[str], list[str]]:
    """Pakete girecek dosyalari secer.

    Returns:
        (dosyalar, disarida birakilan sirlar, atlanan buyuk dosyalar)
    """
    workspace = workspace.resolve()
    included: list[Path] = []
    secrets: list[str] = []
    too_large: list[str] = []

    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(workspace).as_posix()
        except ValueError:  # pragma: no cover - rglob disina cikamaz
            continue

        if _is_noise(relative, path.name):
            continue
        if _is_secret(relative, path.name):
            secrets.append(relative)
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            too_large.append(relative)
            continue
        included.append(path)

    return included, secrets, too_large


def _content_tree(workspace: Path, files: list[Path], *, limit: int = 24) -> list[str]:
    """Paket icerigini ust duzey klasor sayilariyla ozetler.

    Dosya dosya listelemek yuzlerce satir eder ve kimse okumaz; klasor basina
    "kac dosya, ne kadar" ozeti paketin seklini bir bakista gosterir.
    """
    groups: dict[str, tuple[int, int]] = {}
    for path in files:
        relative = path.relative_to(workspace).as_posix()
        head = relative.split("/")[0] if "/" in relative else "(kok)"
        count, size = groups.get(head, (0, 0))
        groups[head] = (count + 1, size + path.stat().st_size)

    ordered = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0]))
    rows = ["| Klasor | Dosya | Boyut |", "|---|---:|---:|"]
    for head, (count, size) in ordered[:limit]:
        rows.append(f"| `{head}` | {count} | {size / 1024:.1f} KB |")
    if len(ordered) > limit:
        rows.append(f"| … | {len(ordered) - limit} klasor daha | |")
    return rows


def build_manifest(
    state: ProjectState,
    workspace: Path,
    files: list[Path],
    secrets: list[str],
    readiness: Readiness,
    goal: str,
) -> str:
    """Paketin icindekileri ve yapilan isi anlatan TESLIMAT.md.

    Bu dosya paketin kapak sayfasidir: zip'i acan kisi once bunu okur, arayuz
    de zip'i acmadan dogrudan bunu gosterir. Bu yuzden yalnizca dosya listesi
    degil, *ne yapildigi* de burada anlatilir.
    """
    counts = state.counts()
    tasks = state.list_tasks()
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")

    lines = [
        "# Teslimat",
        "",
        f"**Proje:** {workspace.name}",
        f"**Tarih:** {stamp}",
    ]
    if goal:
        lines.append(f"**Hedef:** {goal}")
    lines += [
        "",
        "Bu paket DeerX tarafindan uretildi: sartname analiz edildi, mimari",
        "kuruldu, kod yazildi, testler kosuldu ve inceleme yapildi.",
        "",
        "## Durum",
        "",
        readiness.report(),
        "",
        "## Sayilar",
        "",
        "| | |",
        "|---|---|",
        f"| Gereksinim | {counts['requirements']} |",
        f"| Bosluk | {counts['gaps']} |",
        f"| Mimari karar | {counts['decisions']} |",
        f"| Gorev | {counts['tasks_done']}/{counts['tasks']} tamamlandi |",
        f"| Pakete giren proje dosyasi | {len(files)} |",
        "",
    ]

    # --- Neler yapildi: fazlarin kendi ozetleriyle -------------------- #
    ran = [p for p in state.all_phases() if p.status != Status.PENDING]
    if ran:
        total_cost = sum(p.cost_usd for p in ran)
        lines += [
            "## Neler yapildi",
            "",
            "Boru hattinin calistirilan fazlari ve her birinin kendi ozeti:",
            "",
        ]
        for phase_state in ran:
            phase = Phase(phase_state.phase)
            mark = {Status.DONE: "[tamam]", Status.FAILED: "[basarisiz]"}.get(
                phase_state.status, f"[{phase_state.status}]"
            )
            lines.append(f"### {phase.index + 1}. {phase.label} {mark}")
            summary = (phase_state.summary or "").strip()
            lines.append(summary or "_(ozet yok)_")
            if phase_state.cost_usd:
                lines.append(f"\n_Maliyet: ${phase_state.cost_usd:.4f}_")
            lines.append("")
        if total_cost:
            lines += [f"**Toplam model maliyeti: ${total_cost:.4f}**", ""]

    requirements = state.list_requirements()
    if requirements:
        lines += [
            "## Karsilanan gereksinimler",
            "",
            "| Anahtar | Oncelik | Baslik | Kaynak |",
            "|---|---|---|---|",
        ]
        for req in requirements[:60]:
            source = req.source_ref or "—"
            lines.append(f"| {req.key} | {req.priority} | {req.title} | {source} |")
        if len(requirements) > 60:
            lines.append(f"| … | | {len(requirements) - 60} gereksinim daha | |")
        lines.append("")

    done = [t for t in tasks if t.status == Status.DONE]
    if done:
        lines += ["## Tamamlanan gorevler", ""]
        for task in done:
            lines.append(f"- **{task.key}** ({task.lane}) {task.title}")
            if task.acceptance:
                lines.append(f"  - Kabul olcutu: {task.acceptance}")
        lines.append("")

    unfinished = [t for t in tasks if t.status not in {Status.DONE, Status.SKIPPED}]
    if unfinished:
        lines += ["## Tamamlanmayan gorevler", ""]
        lines += [
            f"- **{t.key}** ({t.status}) {t.title}" for t in unfinished
        ]
        lines.append("")

    decisions = state.list_decisions()
    if decisions:
        lines += ["## Mimari kararlar", ""]
        for decision in decisions:
            lines.append(f"- **{decision.key}** {decision.title} → {decision.choice}")
            if decision.rationale:
                lines.append(f"  - Gerekce: {decision.rationale}")
        lines.append("")

    # Paket artifaktlari `belgeler/` altina girer; onceki zip'ler girmez.
    artifacts = [a for a in state.list_artifacts() if a.kind != "package"]
    if artifacts:
        lines += [
            "## Belgeler",
            "",
            "Paketteki `belgeler/` klasorunde:",
            "",
        ]
        lines += [f"- `{a.name}` — {a.summary or a.kind}" for a in artifacts]
        lines.append("")

    if files:
        lines += ["## Paket icerigi", ""]
        lines += _content_tree(workspace, files)
        lines.append("")

    if secrets:
        lines += [
            "## Pakete alinmayan dosyalar",
            "",
            "Asagidaki dosyalar sir icerdigi icin pakete DAHIL EDILMEDI. Projeyi",
            "calistirmak icin bunlari kendiniz olusturmaniz gerekir:",
            "",
        ]
        lines += [f"- `{s}`" for s in secrets]
        lines.append("")

    lines += [
        "## Acik konular",
        "",
    ]
    open_gaps = [g for g in state.list_gaps() if g.status != Status.DONE]
    if open_gaps:
        for gap in open_gaps[:20]:
            lines.append(f"- **{gap.key}** ({gap.severity}/{gap.area}) {gap.title}")
            if gap.recommendation:
                lines.append(f"  - Oneri: {gap.recommendation}")
    else:
        lines.append("Acik bosluk kaydi yok.")

    return "\n".join(lines) + "\n"


def build_package(
    state: ProjectState,
    workspace: Path,
    output_dir: Path,
    *,
    goal: str = "",
    force: bool = False,
    run_id: str = "",
) -> PackageResult:
    """Projeyi zip olarak paketler.

    Args:
        force: Hazirlik denetimi engel bulsa da paketle.

    Raises:
        PackagingNotReady: Engel var ve `force` verilmedi.
    """
    readiness = check_readiness(state)
    if not readiness.ok and not force:
        raise PackagingNotReady(readiness)

    files, secrets, too_large = collect_files(workspace)
    total = sum(f.stat().st_size for f in files)
    if total > MAX_TOTAL_BYTES:
        raise PackagingError(
            f"Paket cok buyuk ({total / 1e6:.0f} MB > {MAX_TOTAL_BYTES / 1e6:.0f} MB). "
            "Gereksiz dosyalari temizleyin."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = _safe_name(workspace.name)
    archive = _unique_path(output_dir, f"{root}-{stamp}", ".zip")

    manifest = build_manifest(state, workspace, files, secrets, readiness, goal)

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(f"{root}/{MANIFEST_NAME}", manifest)
        for path in files:
            relative = path.relative_to(workspace).as_posix()
            zf.write(path, f"{root}/{relative}")
        # Uretilen belgeler ayri bir klasorde; kod agacini kirletmez.
        # Onceki teslimat zip'leri de birer artifakt olarak kayitlidir — onlari
        # atlamak sart: yoksa her paket bir oncekini icine alir ve boyut katlanir.
        for artifact in state.list_artifacts():
            if artifact.kind == "package":
                continue
            source = Path(artifact.path)
            if source.is_file() and not _within(source, output_dir):
                zf.write(source, f"{root}/belgeler/{artifact.name}")
        entry_count = len(zf.namelist())

    total_bytes = archive.stat().st_size
    log.info(t("pipeline.package_written", path=archive, count=entry_count))

    # Kayit tek yerde: CLI, web ve faz ayni sonucu uretsin diye burada yapilir.
    state.add_artifact(
        Artifact(
            name=archive.name,
            kind="package",
            path=str(archive),
            summary=f"{entry_count} dosya · {total_bytes / 1e6:.1f} MB",
        ),
        run_id=run_id,
        phase=str(Phase.PACKAGE),
    )

    return PackageResult(
        path=archive,
        file_count=entry_count,
        total_bytes=total_bytes,
        excluded_secrets=secrets,
        skipped_large=too_large,
        readiness=readiness,
        manifest=manifest,
    )


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Ayni saniyede iki paket uretilirse ikincisi oncekini ezmesin."""
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def read_manifest(archive: Path) -> str:
    """Zip'in icindeki TESLIMAT.md'yi doner; yoksa bos dize.

    Arayuz raporu boylece gosterir — zip'i diske acmadan, ikili icerigi metin
    gibi okumaya calismadan.
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            entry = next(
                (n for n in zf.namelist() if n.rsplit("/", 1)[-1] == MANIFEST_NAME), None
            )
            if entry is None:
                return ""
            return zf.read(entry).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError) as exc:
        log.warning(t("pipeline.package_unreadable", name=archive.name, error=exc))
        return ""


def list_entries(archive: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    """Zip icindeki dosyalarin adi ve boyutu."""
    try:
        with zipfile.ZipFile(archive) as zf:
            return [
                {"name": info.filename, "bytes": info.file_size}
                for info in zf.infolist()
                if not info.is_dir()
            ][:limit]
    except (zipfile.BadZipFile, OSError):
        return []


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
    return cleaned.strip("-") or "proje"


class PackagingError(Exception):
    """Paketleme yapilamadi."""


class PackagingNotReady(PackagingError):
    """Proje teslim edilecek durumda degil."""

    def __init__(self, readiness: Readiness) -> None:
        self.readiness = readiness
        super().__init__(
            readiness.report()
            + "\n\nYine de paketlemek icin --force kullanin."
        )
