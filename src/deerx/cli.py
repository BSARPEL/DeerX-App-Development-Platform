"""DeerX komut satiri arayuzu."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .config import (
    CONFIG_FILENAME,
    DEFAULT_PORT,
    Settings,
    browse_host,
    load_settings,
)
from .errors import DeerXError
from .i18n import set_language, t
from .logging import GLYPHS, EventLog, console, setup_logging
from .pipeline import Orchestrator, Phase, Status


def _early_language() -> str:
    """Typer komutlari KURULMADAN once dili belirler.

    Yardim metinleri dekoratorlerde, yani ICE AKTARMA aninda hesaplanir;
    `Settings` yuklenmesini bekleyemezler. Bu yuzden dil burada hafif bir
    okumayla belirlenir: once ortam degiskeni, sonra calisma alanindaki
    `deerx.toml`.

    Okuma basarisiz olursa Turkce'ye duser. Bir CLI, yapilandirma dosyasi
    bozuk diye ICE AKTARILAMAZ hale gelmemeli -- o durumda kullanicinin
    hatayi duzeltmek icin calistiracagi komut da calismazdi.
    """
    for name in ("DEERX_LANGUAGE", "DEERX_LANG"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        import tomllib

        root = Path(os.environ.get("DEERX_WORKSPACE") or Path.cwd()).resolve()
        for candidate in (root, *root.parents):
            path = candidate / CONFIG_FILENAME
            if path.is_file():
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                block = data.get("deerx") or data.get("praxis") or {}
                return str(block.get("language") or "tr")
    except Exception:  # noqa: BLE001 - dil bulunamadi; varsayilanla devam
        pass
    return "tr"


set_language(_early_language())


app = typer.Typer(
    name="deerx",
    help=t("cli.app"),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

_STATUS_STYLE = {
    Status.DONE: "ok",
    Status.RUNNING: "phase",
    Status.FAILED: "err",
    Status.BLOCKED: "warn",
    Status.CANCELLED: "warn",
    Status.NEEDS_INPUT: "warn",
    Status.SKIPPED: "dim",
    Status.PENDING: "dim",
}


# ---------------------------------------------------------------------- #
# Ortak yardimcilar
# ---------------------------------------------------------------------- #
def _settings(workspace: Path | None = None, **overrides: object) -> Settings:
    try:
        settings = load_settings(workspace, **overrides)
    except DeerXError as exc:
        console.print(f"[err]{t('cli.config_error')}[/err] {exc}")
        raise typer.Exit(1) from exc
    setup_logging(settings.log_level)
    return settings


def _orchestrator(settings: Settings, *, quiet: bool = False) -> Orchestrator:
    events = EventLog(settings.events_path, echo=not quiet)
    return Orchestrator(settings, events=events, stream=not quiet)


TEMPLATE_DIR = Path(__file__).parent / "templates"


def _default_config() -> str:
    """Yeni calisma alanlari icin deerx.toml sablonu."""
    return (TEMPLATE_DIR / "deerx.default.toml").read_text(encoding="utf-8")


def _fail(message: str) -> None:
    console.print(f"[err]{GLYPHS['error']}[/err] {message}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------- #
# init
# ---------------------------------------------------------------------- #
@app.command(help=t("cli.init"))
def init(
    path: Annotated[Path, typer.Argument(help=t("opt.project_dir"))] = Path("."),
    force: Annotated[bool, typer.Option("--force", help=t("opt.force_overwrite"))] = False,
) -> None:
    """Yeni bir DeerX calisma alani kurar (deerx.toml + dizinler)."""
    workspace = path.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / CONFIG_FILENAME

    if config_path.exists() and not force:
        _fail(t("cli.already_exists", path=config_path))

    config_path.write_text(_default_config(), encoding="utf-8")

    env_path = workspace / ".env"
    if not env_path.exists():
        env_path.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")

    settings = load_settings(workspace)
    settings.ensure_dirs()
    (workspace / "docs").mkdir(exist_ok=True)

    console.print(
        Panel(
            "\n".join(
                [
                    t("cli.workspace_ready", path=workspace),
                    "",
                    t("cli.next_steps"),
                    t("cli.step_key"),
                    t("cli.step_spec", path=workspace / "docs"),
                    t("cli.step_run"),
                    "",
                    t("cli.settings_at", path=config_path),
                ]
            ),
            title="deerx init",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------- #
# ingest / search
# ---------------------------------------------------------------------- #
@app.command(help=t("cli.ingest"))
def ingest(
    paths: Annotated[list[Path] | None, typer.Argument(help=t("opt.paths"))] = None,
    force: Annotated[bool, typer.Option("--force", help=t("opt.reindex"))] = False,
) -> None:
    """Dokumanlari ve kodu bilgi tabanina indeksler."""
    settings = _settings()
    with _orchestrator(settings) as orch:
        result = orch.run_phase(Phase.INGEST, sources=list(paths or []), force=True)
        if not result.ok:
            _fail(result.error or t("cli.index_failed"))

        table = Table(title=t("cli.kb"), show_header=True, header_style="bold")
        table.add_column(t("col.document"))
        table.add_column(t("col.kind"))
        table.add_column(t("col.chunk"), justify="right")
        for doc in orch.kb.list_documents()[:60]:
            table.add_row(doc["title"], doc["kind"], str(doc["n_chunks"]))
        console.print(table)
        console.print(f"[ok]{GLYPHS['done']}[/ok] {result.summary}")


@app.command(help=t("cli.search"))
def search(
    query: Annotated[str, typer.Argument(help=t("opt.query"))],
    k: Annotated[int, typer.Option("-k", help=t("opt.count"))] = 6,
    kind: Annotated[list[str] | None, typer.Option("--kind", help=t("opt.kinds"))] = None,
    full: Annotated[bool, typer.Option("--full", help=t("opt.full_chunks"))] = False,
) -> None:
    """Bilgi tabaninda hibrit arama yapar."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        hits = orch.kb.search(query, k=k, kinds=list(kind) if kind else None)
        if not hits:
            console.print(t("cli.no_results", stats=orch.kb.stats()))
            raise typer.Exit(0)
        for index, hit in enumerate(hits, start=1):
            body = hit.text if full else hit.text[:900] + ("…" if len(hit.text) > 900 else "")
            console.print(
                Panel(
                    body,
                    title=f"{index}. {hit.citation()}",
                    subtitle=t("cli.score", score=f"{hit.score:.4f}", kind=hit.kind),
                    border_style="cyan",
                )
            )


# ---------------------------------------------------------------------- #
# run / phase
# ---------------------------------------------------------------------- #
@app.command(help=t("cli.run") + "\n\n" + t("cli.run_detail"))
def run(
    start: Annotated[str, typer.Option("--from", help=t("opt.from_phase"))] = "ingest",
    end: Annotated[str, typer.Option("--to", help=t("opt.to_phase"))] = "plan",
    doc: Annotated[list[Path] | None, typer.Option("--doc", help=t("opt.source"))] = None,
    goal: Annotated[str, typer.Option("--goal", help=t("opt.goal"))] = "",
    brief: Annotated[str | None, typer.Option("--brief", help=t("opt.brief"))] = None,
    force: Annotated[bool, typer.Option("--force", help=t("opt.force_phases"))] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help=t("opt.yes_auto"))] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help=t("opt.dry_run"))] = False,
) -> None:
    """Boru hattini calistirir.

    Varsayilan aralik [bold]ingest → plan[/bold]: analiz, arastirma, bosluk
    degerlendirmesi, mockup, mimari ve gelistirme plani uretilir; kod yazilmaz.
    Kodu da yazdirmak icin [bold]--to review[/bold], dagitima kadar gitmek icin
    [bold]--to live[/bold] verin.

    Bir ajan yalnizca sizin cevaplayabileceginiz bir soru kaydederse boru hatti
    orada durur ve sorulari gosterir. [bold]deerx answer[/bold] ile cevaplayip
    kaldiginiz yerden devam edersiniz.
    """
    mode = "auto" if yes else ("dry-run" if dry_run else None)
    settings = _settings(approval_mode=mode)

    phases = _phase_range(start, end)
    console.print(
        Panel(
            "\n".join(f"  {p.index + 1}. {p.label} · {p.agent_label}" for p in phases)
            + f"\n\n{t('cli.workspace')}: {settings.workspace}"
            + f"\n{t('cli.approval_mode')}: {settings.approval_mode}"
            + f"\n{t('cli.models')}: "
            + t("cli.model_pair", lead=settings.model_lead, worker=settings.model_worker),
            title=t("cli.phases_to_run"),
            border_style="cyan",
        )
    )

    with _orchestrator(settings) as orch:
        report = orch.run(
            phases,
            goal=goal,
            brief=_read_brief(brief),
            sources=list(doc or []),
            force=force,
        )
        _print_report(orch, report)
        _exit_for(report)


@app.command(help=t("cli.phase"))
def phase(
    name: Annotated[str, typer.Argument(help=t("opt.phase_name"))],
    force: Annotated[bool, typer.Option("--force")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Tek bir fazi calistirir."""
    settings = _settings(approval_mode="auto" if yes else None)
    target = _parse_phase(name)
    with _orchestrator(settings) as orch:
        report = orch.run([target], force=force)
        _print_report(orch, report)
        _exit_for(report)


@app.command(help=t("cli.implement"))
def implement(
    task: Annotated[str | None, typer.Option("--task", help=t("opt.task_only"))] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help=t("opt.yes"))] = False,
) -> None:
    """Plandaki gorevleri uygular."""
    settings = _settings(approval_mode="auto" if yes else None)
    with _orchestrator(settings) as orch:
        result = orch.run_phase(Phase.IMPLEMENT, task_key=task, force=True)
        console.print(
            Panel(
                result.summary or t("cli.no_summary"),
                title=f"{t('cli.implementation')} · {result.status}",
                border_style="green" if result.ok else "red",
            )
        )
        if result.error:
            _fail(result.error)


# ---------------------------------------------------------------------- #
# status / tasks / artifacts
# ---------------------------------------------------------------------- #
@app.command(help=t("cli.status"))
def status() -> None:
    """Proje durumunu gosterir."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        goal = orch.state.get_meta("goal", "")
        stats = orch.kb.stats()
        counts = orch.state.counts()

        phase_table = Table(title=t("cli.phases"), show_header=True, header_style="bold")
        phase_table.add_column("#", justify="right")
        phase_table.add_column(t("col.phase"))
        phase_table.add_column(t("col.status"))
        phase_table.add_column(t("col.cost"), justify="right")
        phase_table.add_column(t("col.summary"), overflow="fold", max_width=60)
        for state in orch.state.all_phases():
            ph = Phase(state.phase)
            style = _STATUS_STYLE.get(state.status, "dim")
            phase_table.add_row(
                str(ph.index + 1),
                ph.label,
                f"[{style}]{state.status}[/{style}]",
                f"${state.cost_usd:.3f}" if state.cost_usd else "—",
                (state.summary or "").replace("\n", " ")[:180],
            )
        console.print(phase_table)

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="dim")
        summary.add_column()
        summary.add_row(t("cli.workspace"), str(settings.workspace))
        if goal:
            summary.add_row(t("cli.goal"), goal)
        summary.add_row(
            t("cli.kb"),
            t(
                "cli.kb_stats",
                documents=stats["documents"],
                chunks=stats["chunks"],
                model=stats["embedding_model"],
            ),
        )
        summary.add_row(
            t("cli.records"),
            t(
                "cli.record_stats",
                requirements=counts["requirements"],
                gaps=counts["gaps"],
                decisions=counts["decisions"],
                notes=counts["research_notes"],
            ),
        )
        if counts["questions"]:
            blocking = counts["questions_blocking"]
            tone = "warn" if blocking else "dim"
            summary.add_row(
                t("cli.questions_label"),
                f"[{tone}]"
                + t(
                    "cli.question_stats",
                    open=counts["questions_open"],
                    total=counts["questions"],
                )
                + (t("cli.blocking_suffix", count=blocking) if blocking else "")
                + f"[/{tone}]",
            )
        summary.add_row(
            t("cli.tasks_label"),
            t("cli.task_stats", done=counts["tasks_done"], total=counts["tasks"]),
        )
        summary.add_row(t("cli.artifacts_label"), str(counts["artifacts"]))
        console.print(Panel(summary, title="DeerX", border_style="cyan"))


@app.command(help=t("cli.tasks"))
def tasks(
    status_filter: Annotated[str | None, typer.Option("--status", help=t("opt.task_status"))] = None,
) -> None:
    """Gelistirme gorevlerini listeler."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        items = orch.state.list_tasks(status_filter)
        if not items:
            console.print(t("cli.no_tasks"))
            raise typer.Exit(0)

        ready = {t.key for t in orch.state.ready_tasks()}
        table = Table(show_header=True, header_style="bold")
        table.add_column(t("col.key"))
        table.add_column(t("col.status"))
        table.add_column(t("col.kind"))
        table.add_column(t("col.title"), overflow="fold")
        table.add_column(t("col.deps"))
        for task in items:
            style = _STATUS_STYLE.get(task.status, "dim")
            marker = f" [ok]{GLYPHS['done']}[/ok]" if task.key in ready else ""
            table.add_row(
                task.key + marker,
                f"[{style}]{task.status}[/{style}]",
                task.kind,
                task.title,
                ", ".join(task.deps) or "—",
            )
        console.print(table)
        console.print(f"[dim]{t('cli.ready_legend', glyph=GLYPHS['done'])}[/dim]")


user_app = typer.Typer(help=t("cli.user"), no_args_is_help=True)
app.add_typer(user_app, name="user")


def _auth_store():
    from .web.auth import AuthStore

    settings = _settings()
    settings.ensure_dirs()
    return AuthStore(settings.db_path), settings


def _password_from_stdin() -> str:
    """Parolayi standart girdiden okur; betikler icin.

    `getpass` Windows'ta konsolu DOGRUDAN okur ve boru hattindaki veriyi
    hic gormez: `printf ... | deerx user passwd admin` ciktisiz kilitlenir.
    Betiklerin parolayi guvenli okuyup buraya aktarabilmesi icin ayri bir
    yol lazim.

    Neden arguman degil: arguman `ps` ciktisinda ve Gorev Yoneticisi'nde
    gorunur, kabuk gecmisine yazilir. Standart girdi ikisine de dusmez.
    """
    import sys

    line = sys.stdin.readline()
    if not line:
        _fail(t("cli.password_stdin_empty"))
    # Yalnizca satir sonu atilir. `strip()` bastaki/sondaki bosluklu bir
    # parolayi sessizce baskasina cevirirdi; parola politikasi onu zaten
    # reddediyor ve reddi gormek sessiz degisiklikten iyidir.
    return line.rstrip("\r\n")


def _ask_password(confirm: bool = True) -> str:
    """Parolayi gizli okur. Argumanla almak kabuk gecmisine yazardi."""
    import getpass

    from .web.auth import AuthError, check_password_policy

    while True:
        first = getpass.getpass(t("cli.password"))
        try:
            warning = check_password_policy(first)
        except AuthError as exc:
            console.print(f"[err]{exc}[/err]")
            continue
        if warning:
            console.print(f"[warn]{GLYPHS['warn']} {warning}[/warn]")
        if not confirm:
            return first
        if first != getpass.getpass(t("cli.password_again")):
            console.print(f"[err]{t('cli.password_mismatch')}[/err]")
            continue
        return first


@user_app.command("add", help=t("cli.user_add"))
def user_add(
    username: Annotated[str, typer.Argument(help=t("opt.username_lower"))],
    admin: Annotated[bool, typer.Option("--admin", help=t("opt.admin"))] = False,
    name: Annotated[str, typer.Option("--name", help=t("opt.display_name"))] = "",
    stdin: Annotated[bool, typer.Option("--stdin", help=t("opt.password_stdin"))] = False,
) -> None:
    """Yeni kullanici olusturur. Parola sorulur, argumanla alinmaz."""
    from .web.auth import AuthError

    store, _ = _auth_store()
    try:
        first = not store.is_configured
        password = _password_from_stdin() if stdin else _ask_password()
        if first:
            # Ilk kullanici her zaman yonetici ve silinemez olur; aksi halde
            # sisteme girilemez hale gelebilirdi.
            token = store.issue_setup_token()
            user = store.create_first_admin(token, username, password, name)
            console.print(f"[dim]{t('cli.first_account')}[/dim]")
        else:
            user = store.create_user(
                username, password, role="admin" if admin else "user", display_name=name
            )
    except AuthError as exc:
        store.close()
        _fail(str(exc))
        return
    console.print(
        f"[ok]{GLYPHS['done']}[/ok] "
        + t("cli.user_created", name=user.username, role=user.role)
    )
    if store.last_warning:
        console.print(f"[warn]{GLYPHS['warn']} {store.last_warning}[/warn]")
    store.close()


@user_app.command("list", help=t("cli.user_list"))
def user_list() -> None:
    """Kullanicilari listeler."""
    import datetime as _dt

    store, settings = _auth_store()
    users = store.list_users()
    if not users:
        console.print(t("cli.no_users"))
        store.close()
        return

    table = Table(title=t("cli.users_title", workspace=settings.workspace))
    for column in ("col.username", "col.name", "col.role", "col.status", "col.last_login"):
        table.add_column(t(column))
    for user in users:
        last = (
            _dt.datetime.fromtimestamp(user.last_login).strftime("%d.%m.%Y %H:%M")
            if user.last_login else "—"
        )
        role = f"{user.role}{t('cli.master_suffix') if user.is_master else ''}"
        durum = t("cli.active") if user.is_active else f"[warn]{t('cli.inactive')}[/warn]"
        table.add_row(user.username, user.display_name or "—", role, durum, last)
    console.print(table)
    store.close()


@user_app.command("passwd", help=t("cli.user_passwd"))
def user_passwd(
    username: Annotated[str, typer.Argument(help=t("opt.username"))],
    stdin: Annotated[bool, typer.Option("--stdin", help=t("opt.password_stdin"))] = False,
) -> None:
    """Parolayi degistirir. Acik oturumlarin hepsi duser."""
    from .web.auth import AuthError

    store, _ = _auth_store()
    user = store.find(username)
    if user is None:
        store.close()
        _fail(t("cli.no_such_user", name=username))
        return
    try:
        warning = store.set_password(
            user.id, _password_from_stdin() if stdin else _ask_password()
        )
    except AuthError as exc:
        store.close()
        _fail(str(exc))
        return
    console.print(
        f"[ok]{GLYPHS['done']}[/ok] " + t("cli.password_changed", name=user.username)
    )
    if warning:
        console.print(f"[warn]{GLYPHS['warn']} {warning}[/warn]")
    store.close()


@user_app.command("ensure", help=t("cli.user_ensure"))
def user_ensure(
    username: Annotated[str, typer.Argument(help=t("opt.username_lower"))],
    admin: Annotated[bool, typer.Option("--admin", help=t("opt.admin"))] = True,
    name: Annotated[str, typer.Option("--name", help=t("opt.display_name"))] = "",
    stdin: Annotated[bool, typer.Option("--stdin", help=t("opt.password_stdin"))] = False,
) -> None:
    """Hesabi olusturur ya da parolasini sifirlar; hangisi gerekiyorsa.

    Uc durum tek komutta: hic kullanici yoksa ANA yonetici olarak kurar,
    kullanici yoksa ama baskalari varsa ekler, varsa parolasini
    degistirir. Betiklerin `deerx user list` ciktisini ayristirmasindan
    iyi: o tablonun bicimi Rich surumune bagli ve sessizce degisir.
    """
    from .web.auth import AuthError

    store, _ = _auth_store()
    password = _password_from_stdin() if stdin else _ask_password()
    varolan = store.find(username)
    try:
        if varolan is not None:
            store.set_password(varolan.id, password)
            console.print(
                f"[ok]{GLYPHS['done']}[/ok] "
                + t("cli.password_changed", name=varolan.username)
            )
        elif not store.is_configured:
            # Ilk hesap her zaman ANA yonetici: silinemez, rolu
            # dusurulemez. Aksi halde sisteme girilemez hale gelebilirdi.
            user = store.create_first_admin(
                store.issue_setup_token(), username, password, name
            )
            console.print(f"[dim]{t('cli.first_account')}[/dim]")
            console.print(
                f"[ok]{GLYPHS['done']}[/ok] "
                + t("cli.user_created", name=user.username, role=user.role)
            )
        else:
            user = store.create_user(
                username, password, role="admin" if admin else "user", display_name=name
            )
            console.print(
                f"[ok]{GLYPHS['done']}[/ok] "
                + t("cli.user_created", name=user.username, role=user.role)
            )
    except AuthError as exc:
        store.close()
        _fail(str(exc))
        return
    if store.last_warning:
        console.print(f"[warn]{GLYPHS['warn']} {store.last_warning}[/warn]")
    store.close()


@user_app.command("disable", help=t("cli.user_disable"))
def user_disable(
    username: Annotated[str, typer.Argument(help=t("opt.username"))],
) -> None:
    """Hesabi kapatir. Silmez; acik oturumlari dusurur, giris engellenir."""
    _toggle_user(username, active=False)


@user_app.command("enable", help=t("cli.user_enable"))
def user_enable(
    username: Annotated[str, typer.Argument(help=t("opt.username"))],
) -> None:
    """Kapatilmis hesabi yeniden acar."""
    _toggle_user(username, active=True)


def _toggle_user(username: str, *, active: bool) -> None:
    from .web.auth import AuthError

    store, _ = _auth_store()
    user = store.find(username)
    if user is None:
        store.close()
        _fail(t("cli.no_such_user", name=username))
        return
    try:
        updated = store.set_active(user.id, active)
    except AuthError as exc:
        store.close()
        _fail(str(exc))
        return
    key = "cli.user_enabled" if updated.is_active else "cli.user_disabled"
    console.print(f"[ok]{GLYPHS['done']}[/ok] " + t(key, name=updated.username))
    store.close()


@user_app.command("remove", help=t("cli.user_delete"))
def user_remove(
    username: Annotated[str, typer.Argument(help=t("opt.username"))],
    yes: Annotated[bool, typer.Option("--yes", "-y", help=t("opt.no_confirm"))] = False,
) -> None:
    """Kullaniciyi siler. Ana yonetici silinemez."""
    from .web.auth import AuthError

    store, _ = _auth_store()
    user = store.find(username)
    if user is None:
        store.close()
        _fail(t("cli.no_such_user", name=username))
        return
    if not yes and not typer.confirm(t("cli.confirm_delete", name=user.username)):
        store.close()
        return
    try:
        store.delete_user(user.id)
    except AuthError as exc:
        store.close()
        _fail(str(exc))
        return
    console.print(f"[ok]{GLYPHS['done']}[/ok] " + t("cli.user_deleted", name=user.username))
    store.close()


@app.command(help=t("cli.package") + "\n\n" + t("cli.package_detail"))
def package(
    force: Annotated[bool, typer.Option("--force", help=t("opt.package_force"))] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help=t("opt.out_dir"))] = None,
) -> None:
    """Uretilen projeyi teslim edilebilir bir zip olarak paketler.

    Once hazirlik denetimi yapilir: tamamlanmamis veya basarisiz gorev,
    cevaplanmamis bloke edici soru varsa paketleme durur. Sirlar ([bold].env[/bold],
    anahtar dosyalari) pakete ASLA girmez.
    """
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        from .pipeline.packaging import PackagingError, PackagingNotReady, build_package

        try:
            result = build_package(
                orch.state,
                settings.workspace,
                output.resolve() if output else settings.deliveries_dir,
                goal=orch.state.get_meta("goal", ""),
                force=force,
            )
        except PackagingNotReady as exc:
            hint = t("cli.package_force_hint")
            console.print(
                Panel(
                    exc.readiness.report() + "\n\n" + hint,
                    title=f"{GLYPHS['warn']} {t('cli.not_ready')}",
                    border_style="yellow",
                )
            )
            raise typer.Exit(2) from None
        except PackagingError as exc:
            _fail(str(exc))
            return

        body = [
            f"[ok]{GLYPHS['done']}[/ok] "
            + t(
                "cli.package_files",
                count=result.file_count,
                megabytes=f"{result.total_bytes / 1e6:.1f}",
            ),
            "",
            f"[bold]{result.path}[/bold]",
        ]
        if result.excluded_secrets:
            body += [
                "",
                t("cli.secrets_excluded", count=len(result.excluded_secrets)),
            ]
            body += [f"  {name}" for name in result.excluded_secrets[:10]]
        if result.skipped_large:
            body += ["", t("cli.large_skipped", count=len(result.skipped_large))]
        if result.readiness.warnings:
            body += ["", t("cli.warnings")]
            body += [f"  {i.message}" for i in result.readiness.warnings]

        console.print(
            Panel("\n".join(body), title=t("cli.delivery_package"), border_style="green")
        )


@app.command(help=t("cli.questions"))
def questions(
    all_questions: Annotated[bool, typer.Option("--all", help=t("opt.show_answered"))] = False,
) -> None:
    """Ajanlarin size sordugu acik sorulari listeler."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        items = orch.state.list_questions() if all_questions else orch.state.list_questions("open")
        if not items:
            message = (
                t("cli.no_questions") if all_questions else t("cli.no_open_questions")
            )
            console.print(f"[ok]{GLYPHS['done']}[/ok] {message}")
            raise typer.Exit(0)

        table = Table(show_header=True, header_style="bold")
        table.add_column(t("col.key"))
        table.add_column(t("col.status"))
        table.add_column(t("col.blocking"), justify="center")
        table.add_column(t("col.question"), overflow="fold")
        table.add_column(t("cli.answer_or_assumption"), overflow="fold")
        for question in items:
            style = {"answered": "ok", "skipped": "dim"}.get(question.status, "warn")
            resolution = question.answer or (
                t("cli.assumption_prefix", text=question.suggestion)
                if question.suggestion
                else ""
            )
            table.add_row(
                question.key,
                f"[{style}]{question.status}[/{style}]",
                GLYPHS["warn"] if question.blocking else "",
                question.question + (f"\n[dim]{question.why}[/dim]" if question.why else ""),
                resolution,
            )
        console.print(table)

        blocking = orch.state.open_blocking_questions()
        if blocking:
            console.print(
                t("cli.blocking_count", count=len(blocking))
                + " "
                + t("cli.answer_hint", key=blocking[0].key)
            )


@app.command(help=t("cli.answer"))
def answer(
    key: Annotated[str, typer.Argument(help=t("opt.question_key"))],
    text: Annotated[str, typer.Argument(help=t("opt.answer_text"))] = "",
    from_file: Annotated[Path | None, typer.Option("--from-file", "-f", help=t("opt.answer_file"))] = None,
) -> None:
    """Bir soruyu cevaplar; cevap bilgi tabanina da yazilir."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        body = _read_answer(text, from_file)
        if not body.strip():
            _fail(t("cli.empty_answer"))
        question = orch.answer_question(key, body.strip())
        if question is None:
            _fail(t("cli.no_such_question_hint", key=key.upper()))
            return
        console.print(
            f"[ok]{GLYPHS['done']}[/ok] " + t("cli.answered", key=question.key)
        )
        _report_remaining(orch)


@app.command(help=t("cli.skip"))
def skip(
    key: Annotated[str, typer.Argument(help=t("opt.question_key"))],
    assumption: Annotated[str, typer.Option("--assumption", "-a", help=t("opt.assumption"))] = "",
) -> None:
    """Soruyu atlar; ajanlar belirtilen varsayimla ilerler."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        question = orch.skip_question(key, assumption)
        if question is None:
            _fail(t("cli.no_such_question", key=key.upper()))
            return
        note = question.suggestion or t("cli.own_assumption")
        console.print(
            f"[warn]{GLYPHS['warn']}[/warn] "
            + t("cli.skipped", key=question.key, assumption=note)
        )
        _report_remaining(orch)


def _report_remaining(orch: Orchestrator) -> None:
    remaining = orch.state.open_blocking_questions()
    if remaining:
        console.print(
            t(
                "cli.more_waiting",
                count=len(remaining),
                keys=", ".join(q.key for q in remaining),
            )
        )
    else:
        console.print(
            f"[ok]{GLYPHS['done']}[/ok] "
            + t("cli.no_pending_left")
            + " "
            + t("cli.continue_hint")
        )


@app.command(help=t("cli.artifacts"))
def artifacts(
    name: Annotated[str | None, typer.Argument(help=t("opt.artifact_name"))] = None,
) -> None:
    """Uretilen ciktilari listeler veya birini goruntuler."""
    settings = _settings()
    with _orchestrator(settings, quiet=True) as orch:
        items = orch.state.list_artifacts()
        if not items:
            console.print(t("cli.no_artifacts"))
            raise typer.Exit(0)

        if name is None:
            table = Table(show_header=True, header_style="bold")
            table.add_column(t("col.name"))
            table.add_column(t("col.kind"))
            table.add_column(t("col.summary"), overflow="fold")
            for artifact in items:
                table.add_row(artifact.name, artifact.kind, artifact.summary)
            console.print(table)
            console.print(t("cli.directory", path=settings.artifacts_dir))
            raise typer.Exit(0)

        match = next((a for a in items if a.name == name), None)
        if match is None:
            _fail(
                t(
                    "cli.artifact_not_found",
                    name=name,
                    available=", ".join(a.name for a in items),
                )
            )
            return
        path = Path(match.path)
        if not path.is_file():
            _fail(t("cli.file_missing", path=path))
            return
        content = path.read_text(encoding="utf-8")
        if path.suffix in {".md", ".markdown"}:
            console.print(Markdown(content))
        else:
            console.print(content)


# ---------------------------------------------------------------------- #
# mcp / doctor
# ---------------------------------------------------------------------- #
@app.command(help=t("cli.serve") + "\n\n" + t("cli.serve_detail"))
def serve(
    host: Annotated[str, typer.Option("--host", help=t("opt.host"))] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help=t("opt.port"))] = DEFAULT_PORT,
    workspace: Annotated[Path | None, typer.Option("--workspace", help=t("opt.workspace"))] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help=t("opt.open_browser"))] = True,
) -> None:
    """Web arayuzunu baslatir.

    Panodan boru hattini calistirabilir, canli olay akisini izleyebilir,
    onay isteklerini cevaplayabilir ve uretilen ciktilari goruntuleyebilirsiniz.
    """
    settings = _settings(workspace)
    from .web.app import serve as run_server

    if open_browser:
        import threading
        import webbrowser

        url = f"http://{browse_host(host)}:{port}"
        # Sunucu ayaga kalkana kadar kisa bir gecikme; erken acilan sekme bos gelir.
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        run_server(settings, host=host, port=port)
    except DeerXError as exc:
        # Yapilandirma reddi bir cokme degil, anlatilacak bir durum; yigin
        # izi basmak kullaniciya ne yapacagini soylemez.
        _fail(str(exc))


@app.command(help=t("cli.mcp"))
def mcp(
    workspace: Annotated[Path | None, typer.Option("--workspace", help=t("opt.mcp_workspace"))] = None,
) -> None:
    """MCP sunucusunu stdio uzerinde calistirir."""
    if workspace is not None:
        os.environ["DEERX_WORKSPACE"] = str(workspace.resolve())
    from .mcp_server.server import main as run_server

    run_server()


@app.command(help=t("cli.setup"))
def setup(
    path: Annotated[Path, typer.Argument(help=t("opt.project_dir"))] = Path("."),
    no_deps: Annotated[bool, typer.Option("--no-deps", help=t("opt.setup_no_deps"))] = False,
    no_searxng: Annotated[bool, typer.Option("--no-searxng", help=t("opt.setup_no_searxng"))] = False,
    with_embedding_model: Annotated[
        bool, typer.Option("--with-embedding-model", help=t("opt.setup_embed_model"))
    ] = False,
) -> None:
    """Projenin ihtiyaci olan her seyi kurar.

    `doctor` NE eksik oldugunu soyler, `setup` eksigi KAPATIR. Ayrim
    bilincli: doctor hicbir seye dokunmaz.
    """
    from . import setup as kurulum

    workspace = path.resolve()
    # Calisma alani once kurulur: sonraki adimlarin ayarlari oradan gelir.
    ilk = kurulum.calisma_alani(workspace, kur=True)
    settings = load_settings(workspace)
    setup_logging(settings.log_level)

    adimlar = [ilk] + [
        kurulum.bagimliliklar(kur=not no_deps),
        kurulum.docker(),
        kurulum.searxng(settings, kur=not no_searxng),
        kurulum.tarayici(settings),
        kurulum.model_ucu(settings),
        kurulum.gomme_modeli(settings, indir=with_embedding_model),
    ]

    isaret = {"ok": "[ok]✓[/ok]", "kuruldu": "[ok]+[/ok]",
              "uyari": "[warn]![/warn]", "eksik": "[err]✗[/err]"}
    table = Table(title=t("cli.setup_title"), show_header=True, header_style="bold")
    table.add_column("", justify="center")
    table.add_column(t("col.step"))
    table.add_column(t("col.detail"), overflow="fold")
    for adim in adimlar:
        table.add_row(isaret[adim.durum], adim.ad, adim.detay)
    console.print(table)

    for adim in adimlar:
        if adim.komut:
            console.print(f"  [dim]{adim.ad}:[/dim] [bold]{adim.komut}[/bold]")

    # Kurulan ornegi kullanmamak, bosa kurmak olurdu.
    searx = next(a for a in adimlar if a.anahtar == "step.searxng")
    if searx.durum in ("ok", "kuruldu") and kurulum.searxng_secildi(workspace):
        console.print(f"[ok]{GLYPHS['done']}[/ok] {t('cli.setup_provider_switched')}")

    sayim = kurulum.ozet(adimlar)
    console.print(
        f"[ok]{GLYPHS['done']}[/ok] "
        + t("cli.setup_done", installed=sayim["kuruldu"], ok=sayim["ok"])
    )
    if sayim["uyari"]:
        console.print(f"[warn]{GLYPHS['warn']}[/warn] "
                      + t("cli.setup_warned", count=sayim["uyari"]))
    if sayim["eksik"]:
        console.print(f"[err]{GLYPHS['error']}[/err] "
                      + t("cli.setup_blocked", count=sayim["eksik"]))
        raise typer.Exit(1)


@app.command(help=t("cli.doctor"))
def doctor() -> None:
    """Ortami kontrol eder: anahtar, bagimliliklar, bilgi tabani."""
    settings = _settings()
    table = Table(show_header=True, header_style="bold")
    table.add_column(t("col.check"))
    table.add_column(t("col.status"))
    table.add_column(t("col.detail"), overflow="fold")

    def row(label: str, ok: bool, detail: str) -> None:
        table.add_row(label, f"[ok]{GLYPHS['done']}[/ok]" if ok else f"[err]{GLYPHS['error']}[/err]", detail)

    row(t("cli.provider"), True, settings.provider)
    if settings.provider == "openai":
        row(
            t("cli.model_endpoint"),
            bool(settings.openai_base_url),
            settings.openai_base_url or t("cli.undefined"),
        )
        row(
            t("cli.models"),
            True,
            t("cli.model_pair", lead=settings.model_lead, worker=settings.model_worker),
        )
        row(t("cli.connection"), *_probe_endpoint(settings))
    else:
        row(
            "ANTHROPIC_API_KEY",
            bool(settings.anthropic_api_key),
            t("cli.defined") if settings.anthropic_api_key else t("cli.add_to_env"),
        )
    row(
        "deerx.toml",
        (settings.workspace / CONFIG_FILENAME).is_file(),
        str(settings.workspace / CONFIG_FILENAME),
    )

    for module, hint in (
        ("anthropic", "uv add anthropic"),
        ("mcp", "uv add mcp"),
        ("numpy", "uv add numpy"),
        ("fastembed", t("cli.hint_fastembed")),
        ("pypdf", t("cli.hint_pypdf")),
        ("docx", t("cli.hint_docx")),
        ("bs4", t("cli.hint_soup")),
        ("playwright", t("cli.hint_playwright")),
    ):
        try:
            __import__(module)
            row(module, True, t("cli.installed"))
        except ImportError:
            row(module, False, hint)

    try:
        with _orchestrator(settings, quiet=True) as orch:
            stats = orch.kb.stats()
            row(
                t("cli.kb"),
                stats["chunks"] > 0,
                t(
                    "cli.kb_stats_fts",
                    documents=stats["documents"],
                    chunks=stats["chunks"],
                    fts=t("cli.on") if stats["fts"] else t("cli.off"),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        row(t("cli.kb"), False, str(exc))

    console.print(table)


# ---------------------------------------------------------------------- #
# Ic yardimcilar
# ---------------------------------------------------------------------- #
def _parse_phase(name: str) -> Phase:
    try:
        return Phase(name.strip().lower())
    except ValueError:
        _fail(t("cli.unknown_phase", name=name, options=", ".join(p for p in Phase)))
        raise  # ulasilmaz; tip denetleyicisi icin


def _phase_range(start: str, end: str) -> list[Phase]:
    first, last = _parse_phase(start), _parse_phase(end)
    if first.index > last.index:
        _fail(t("cli.phase_order", start=start, end=end))
    return [p for p in Phase.ordered() if first.index <= p.index <= last.index]


def _probe_endpoint(settings: Settings) -> tuple[bool, str]:
    """OpenAI-uyumlu ucun ayakta olup olmadigini ve modeli sunup sunmadigini bakar."""
    import httpx

    base = (settings.openai_base_url or "").rstrip("/")
    if not base:
        return False, t("cli.base_undefined")
    headers = (
        {"Authorization": f"Bearer {settings.openai_api_key}"}
        if settings.openai_api_key
        else {}
    )
    try:
        response = httpx.get(f"{base}/models", headers=headers, timeout=8.0)
        response.raise_for_status()
        served = [m.get("id") for m in response.json().get("data", [])]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return False, t("cli.auth_refused")
        return False, f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return False, t("cli.unreachable", error=exc)

    missing = [m for m in {settings.model_lead, settings.model_worker} if m not in served]
    if missing:
        return False, t(
            "cli.model_not_served",
            missing=", ".join(missing),
            served=", ".join(served),
        )
    return True, t("cli.models_served", count=len(served), served=", ".join(served))


def _read_brief(value: str | None) -> str | None:
    """`--brief` degerini cozer. `@yol` verilirse icerik dosyadan okunur."""
    if value is None:
        return None
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        if not path.is_file():
            _fail(t("cli.brief_missing", path=path))
        return path.read_text(encoding="utf-8")
    return value


def _read_answer(text: str, from_file: Path | None) -> str:
    """Cevap metnini cozer.

    `--brief` gibi `@` on ekiyle dosya okumaz: bir cevap pekala `@` ile
    baslayabilir (`@firma.com adresine gider`) ve bunu dosya yolu saymak
    komutu kirardi. Dosyadan okuma acik bir bayrakla istenir.
    """
    if from_file is not None:
        if not from_file.is_file():
            _fail(t("cli.answer_file_missing", path=from_file))
        return from_file.read_text(encoding="utf-8").strip()
    return text.strip()


def _exit_for(report: object) -> None:
    """Kosu sonucuna gore cikis kodu.

    0 = tamam, 1 = basarisiz, 2 = kullanicidan cevap bekleniyor. Ucuncu kod
    betiklerin "hata" ile "bilgi bekleniyor" durumunu ayirt etmesini saglar.
    """
    from .pipeline import RunReport

    assert isinstance(report, RunReport)
    if report.needs_input:
        raise typer.Exit(2)
    if not report.ok:
        raise typer.Exit(1)


def _print_report(orch: Orchestrator, report: object) -> None:
    from .pipeline import RunReport

    assert isinstance(report, RunReport)
    table = Table(title=t("cli.run_summary"), show_header=True, header_style="bold")
    table.add_column(t("col.phase"))
    table.add_column(t("col.status"))
    table.add_column(t("col.cost"), justify="right")
    table.add_column(t("col.note"), overflow="fold", max_width=70)
    for result in report.phases:
        style = _STATUS_STYLE.get(result.status, "dim")
        note = result.error or (result.summary or "").replace("\n", " ")[:200]
        table.add_row(
            result.label,
            f"[{style}]{result.status}[/{style}]",
            f"${result.cost:.4f}" if result.cost else "—",
            note,
        )
    console.print(table)

    counts = orch.state.counts()
    console.print(
        t("cli.total_cost", amount=f"{report.total_cost:.4f}")
        + " · "
        + t(
            "cli.run_counts",
            requirements=counts["requirements"],
            gaps=counts["gaps"],
            tasks=counts["tasks"],
            artifacts=counts["artifacts"],
        )
    )
    if orch._client is not None:  # noqa: SLF001 - ayni paket
        console.print(f"[dim]{orch.client.usage_summary()}[/dim]")

    artifacts_list = orch.state.list_artifacts()
    if artifacts_list:
        console.print(t("cli.artifacts_at", path=orch.settings.artifacts_dir))
        for artifact in artifacts_list:
            console.print(
                f"  [ok]{GLYPHS['default']}[/ok] {artifact.name} — "
                f"{artifact.summary or artifact.kind}"
            )

    if report.needs_input:
        _print_pending_questions(orch)


def _print_pending_questions(orch: Orchestrator) -> None:
    """Boru hattini durduran sorulari ve nasil cevaplanacagini gosterir."""
    pending = orch.state.open_blocking_questions()
    if not pending:
        return

    body = []
    for question in pending:
        body.append(f"[bold]{question.key}[/bold]  {question.question}")
        if question.why:
            body.append(f"   [dim]{t('cli.why', text=question.why)}[/dim]")
        if question.suggestion:
            body.append(
                f"   [dim]{t('cli.suggested_assumption', text=question.suggestion)}[/dim]"
            )
        body.append("")
    body.append(t("cli.to_answer"))
    body.append(t("cli.answer_example", key=pending[0].key))
    body.append(t("cli.to_skip"))
    body.append(f"  deerx skip {pending[0].key}")
    body.append("")
    body.append(t("cli.then_continue"))

    console.print(
        Panel(
            "\n".join(body),
            title=f"{GLYPHS['needs_input']} {t('cli.needs_your_answer')}",
            border_style="yellow",
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
