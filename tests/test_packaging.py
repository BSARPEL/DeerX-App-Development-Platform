"""Teslimat paketleme: hazirlik kapisi, dosya secimi, sir dislama."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from deerx.pipeline.models import Artifact, Gap, Phase, Question, Requirement, Status, Task
from deerx.pipeline.packaging import (
    PackagingNotReady,
    build_package,
    check_readiness,
    collect_files,
)


@pytest.fixture
def project(settings, state):
    """Teslime hazir, kucuk ama gercekci bir proje."""
    workspace = settings.workspace
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "src" / "main.py").write_text("def health(): return 'ok'\n", encoding="utf-8")
    (workspace / "tests").mkdir(exist_ok=True)
    (workspace / "tests" / "test_main.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Proje\n", encoding="utf-8")

    state.set_meta("goal", "Kucuk bir API")
    state.add_requirement(Requirement(key="REQ-001", title="Saglik ucu", priority="must"))
    state.add_task(Task(key="T-001", title="API", lane="backend", status=Status.DONE,
                        acceptance="pytest gecer"))
    for phase in (Phase.QA, Phase.REVIEW):
        state.start_phase(phase)
        state.finish_phase(phase, summary="tamam")
    return settings


class TestReadiness:
    def test_ready_project_has_no_blockers(self, project, state):
        readiness = check_readiness(state)
        assert readiness.ok
        assert not readiness.blockers

    def test_empty_plan_blocks(self, settings, state):
        assert not check_readiness(state).ok
        assert any("Plan bos" in i.message for i in check_readiness(state).blockers)

    def test_failed_task_blocks(self, project, state):
        state.add_task(Task(key="T-002", title="bozuk", status=Status.FAILED))
        readiness = check_readiness(state)
        assert not readiness.ok
        assert any("T-002" in i.message for i in readiness.blockers)

    def test_unfinished_task_blocks(self, project, state):
        state.add_task(Task(key="T-003", title="yarim", status=Status.PENDING))
        assert not check_readiness(state).ok

    def test_blocking_question_blocks(self, project, state):
        state.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        readiness = check_readiness(state)
        assert not readiness.ok
        assert any("Q-001" in i.message for i in readiness.blockers)

    def test_open_critical_gap_is_a_warning_not_a_blocker(self, project, state):
        """Kritik bosluk teslimi anlamsiz kilmaz; ama gorunur olmali."""
        state.add_gap(Gap(key="GAP-001", title="Sir yonetimi", severity="critical"))
        readiness = check_readiness(state)
        assert readiness.ok
        assert any("GAP-001" in i.message for i in readiness.warnings)

    def test_missing_qa_phase_is_a_warning(self, settings, state):
        state.add_task(Task(key="T-001", title="x", status=Status.DONE))
        readiness = check_readiness(state)
        assert any("QA" in i.message for i in readiness.warnings)


class TestFileSelection:
    def test_secrets_are_excluded(self, settings):
        workspace = settings.workspace
        (workspace / ".env").write_text("DB_PASSWORD=gizli\n", encoding="utf-8")
        (workspace / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
        (workspace / "id_rsa").write_text("anahtar", encoding="utf-8")
        (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")

        files, secrets, _ = collect_files(workspace)
        names = {f.name for f in files}
        assert "app.py" in names
        assert not {".env", "deploy.pem", "id_rsa"} & names
        assert set(secrets) >= {".env", "deploy.pem", "id_rsa"}

    def test_env_example_is_kept(self, settings):
        """Ornek dosya sirsizdir ve kuruluma yardim eder."""
        (settings.workspace / ".env.example").write_text("DB_PASSWORD=\n", encoding="utf-8")
        files, secrets, _ = collect_files(settings.workspace)
        assert ".env.example" in {f.name for f in files}
        assert ".env.example" not in secrets

    def test_build_noise_is_excluded(self, settings):
        workspace = settings.workspace
        for folder in ("node_modules/pkg", ".git", "__pycache__", ".venv/lib"):
            (workspace / folder).mkdir(parents=True, exist_ok=True)
            (workspace / folder / "dosya.txt").write_text("gurultu", encoding="utf-8")
        (workspace / "gercek.py").write_text("x = 1\n", encoding="utf-8")

        relatives = {
            f.relative_to(workspace).as_posix() for f in collect_files(workspace)[0]
        }
        assert "gercek.py" in relatives
        assert not any(
            part in r for r in relatives
            for part in ("node_modules", ".git/", "__pycache__", ".venv")
        )

    def test_deerx_data_dir_is_excluded(self, settings):
        """Proje hafizasi ve gomme veritabani teslimat degildir."""
        settings.ensure_dirs()
        (settings.data_dir / "deerx.db").write_bytes(b"veritabani")
        relatives = {
            f.relative_to(settings.workspace).as_posix()
            for f in collect_files(settings.workspace)[0]
        }
        assert not any(r.startswith(".deerx") for r in relatives)


class TestBuildPackage:
    def test_refuses_when_not_ready(self, settings, state):
        with pytest.raises(PackagingNotReady) as exc:
            build_package(state, settings.workspace, settings.deliveries_dir)
        assert "Plan bos" in str(exc.value)

    def test_force_packages_anyway(self, settings, state):
        (settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        result = build_package(
            state, settings.workspace, settings.deliveries_dir, force=True
        )
        assert result.path.is_file()
        assert not result.readiness.ok  # engel vardi ama zorlandi

    def test_zip_contents(self, project, state):
        settings = project
        result = build_package(
            state, settings.workspace, settings.deliveries_dir, goal="Kucuk bir API"
        )
        with zipfile.ZipFile(result.path) as zf:
            names = zf.namelist()
            root = settings.workspace.name

        assert f"{root}/TESLIMAT.md" in names
        assert any(n.endswith("src/main.py") for n in names)
        assert any(n.endswith("tests/test_main.py") for n in names)

    def test_manifest_lists_excluded_secrets(self, project, state):
        settings = project
        (settings.workspace / ".env").write_text("SECRET=x\n", encoding="utf-8")
        result = build_package(state, settings.workspace, settings.deliveries_dir)

        with zipfile.ZipFile(result.path) as zf:
            manifest = zf.read(f"{settings.workspace.name}/TESLIMAT.md").decode("utf-8")
        assert ".env" in manifest
        assert "DAHIL EDILMEDI" in manifest

    def test_no_secret_value_leaks_into_the_archive(self, project, state):
        """En ciddi risk: bir sirrin pakete girmesi."""
        settings = project
        (settings.workspace / ".env").write_text("DB_PASSWORD=cok-gizli\n", encoding="utf-8")
        (settings.workspace / "deploy.pem").write_text("GIZLI-ANAHTAR-ICERIGI", encoding="utf-8")

        result = build_package(state, settings.workspace, settings.deliveries_dir)
        with zipfile.ZipFile(result.path) as zf:
            blob = b"".join(zf.read(n) for n in zf.namelist())
        assert b"cok-gizli" not in blob
        assert b"GIZLI-ANAHTAR-ICERIGI" not in blob

    def test_artifacts_go_into_a_separate_folder(self, project, state):
        settings = project
        settings.ensure_dirs()
        report = settings.artifacts_dir / "mimari.md"
        report.write_text("# Mimari\n", encoding="utf-8")
        state.add_artifact(
            Artifact(name="mimari.md", kind="architecture", path=str(report), summary="x")
        )

        result = build_package(state, settings.workspace, settings.deliveries_dir)
        with zipfile.ZipFile(result.path) as zf:
            assert f"{settings.workspace.name}/belgeler/mimari.md" in zf.namelist()

    def test_archive_name_carries_project_and_timestamp(self, project, state):
        settings = project
        result = build_package(state, settings.workspace, settings.deliveries_dir)
        assert result.path.suffix == ".zip"
        assert result.path.stem.startswith(settings.workspace.name[:4])


class TestPhaseIntegration:
    def _orchestrator(self, settings):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        return Orchestrator(settings, events=EventLog(None, echo=False), stream=False)

    def test_package_phase_needs_no_model(self, project):
        """Paketleme deterministiktir: API anahtari olmadan calismali."""
        settings = project
        settings.anthropic_api_key = None
        settings.openai_base_url = None

        with self._orchestrator(settings) as orch:
            result = orch.run_phase(Phase.PACKAGE)
            assert result.ok and result.status == Status.DONE
            assert Path(result.details["path"]).is_file()

    def test_package_phase_blocks_when_not_ready(self, settings):
        with self._orchestrator(settings) as orch:
            result = orch.run_phase(Phase.PACKAGE)
            assert result.status == Status.BLOCKED
            assert "teslim edilecek durumda degil" in (result.error or "")

    def test_package_is_registered_as_an_artifact(self, project):
        settings = project
        with self._orchestrator(settings) as orch:
            orch.run_phase(Phase.PACKAGE)
            kinds = {a.kind for a in orch.state.list_artifacts()}
            assert "package" in kinds

    def test_package_comes_after_review_before_staging(self):
        assert Phase.REVIEW.index < Phase.PACKAGE.index < Phase.STAGING.index


class TestNoiseInNestedFolders:
    """Bir monorepo'da artiklar kokte degil, alt klasorlerde durur."""

    def test_nested_build_folders_are_excluded(self, settings):
        workspace = settings.workspace
        for folder in (
            "frontend/node_modules/react",
            "apps/api/.venv/lib",
            "src/pkg/__pycache__",
            "services/worker/.pytest_cache",
            "libs/core.egg-info",
        ):
            (workspace / folder).mkdir(parents=True, exist_ok=True)
            (workspace / folder / "artik.txt").write_text("gurultu", encoding="utf-8")
        (workspace / "frontend" / "src").mkdir(parents=True, exist_ok=True)
        (workspace / "frontend" / "src" / "App.tsx").write_text("x", encoding="utf-8")

        relatives = {
            f.relative_to(workspace).as_posix() for f in collect_files(workspace)[0]
        }
        assert "frontend/src/App.tsx" in relatives
        assert not [r for r in relatives if r.endswith("artik.txt")], relatives

    def test_nested_secret_is_still_excluded(self, settings):
        nested = settings.workspace / "deploy" / "prod"
        nested.mkdir(parents=True)
        (nested / ".env").write_text("TOKEN=x\n", encoding="utf-8")
        files, secrets, _ = collect_files(settings.workspace)
        assert "deploy/prod/.env" in secrets
        assert not [f for f in files if f.name == ".env"]


class TestRepeatedPackaging:
    def test_previous_package_is_not_embedded_in_the_next(self, project, state):
        """Iki kez paketlemek paketi ic ice sarmalamamali."""
        settings = project
        first = build_package(state, settings.workspace, settings.deliveries_dir)
        second = build_package(state, settings.workspace, settings.deliveries_dir)

        with zipfile.ZipFile(second.path) as zf:
            names = zf.namelist()
        assert not [n for n in names if n.endswith(".zip")], names
        assert first.path.name not in "".join(names)
        # Boyut katlanmamali.
        assert second.total_bytes < first.total_bytes * 2

    def test_two_packages_in_the_same_moment_do_not_overwrite(self, project, state):
        settings = project
        first = build_package(state, settings.workspace, settings.deliveries_dir)
        second = build_package(state, settings.workspace, settings.deliveries_dir)
        assert first.path != second.path
        assert first.path.is_file() and second.path.is_file()

    def test_build_package_registers_the_artifact_itself(self, project, state):
        """CLI, web ve faz ayni kaydi almali; kayit tek yerde yapilir."""
        result = build_package(state, project.workspace, project.deliveries_dir)
        packages = [a for a in state.list_artifacts() if a.kind == "package"]
        assert [a.name for a in packages] == [result.path.name]

    def test_file_count_matches_the_archive(self, project, state):
        result = build_package(state, project.workspace, project.deliveries_dir)
        with zipfile.ZipFile(result.path) as zf:
            assert result.file_count == len(zf.namelist())


class TestManifestReport:
    def test_manifest_describes_what_was_done(self, project, state):
        settings = project
        state.finish_phase(Phase.PLAN, summary="4 gorev planlandi", cost_usd=0.12)
        result = build_package(state, settings.workspace, settings.deliveries_dir)

        assert "## Neler yapildi" in result.manifest
        assert "4 gorev planlandi" in result.manifest
        assert "Paket icerigi" in result.manifest
        assert "Karsilanan gereksinimler" in result.manifest
        assert "REQ-001" in result.manifest

    def test_manifest_lists_architecture_decisions(self, project, state):
        from deerx.pipeline.models import Decision

        state.add_decision(
            Decision(key="ADR-001", title="Veritabani", choice="PostgreSQL",
                     rationale="Iliskisel veri")
        )
        result = build_package(state, project.workspace, project.deliveries_dir)
        assert "ADR-001" in result.manifest
        assert "PostgreSQL" in result.manifest

    def test_read_manifest_returns_the_report_from_the_zip(self, project, state):
        from deerx.pipeline.packaging import read_manifest

        result = build_package(state, project.workspace, project.deliveries_dir)
        assert read_manifest(result.path) == result.manifest

    def test_read_manifest_survives_a_broken_archive(self, settings):
        from deerx.pipeline.packaging import read_manifest

        broken = settings.deliveries_dir / "bozuk.zip"
        broken.write_bytes(b"bu bir zip degil")
        assert read_manifest(broken) == ""

    def test_list_entries_reports_names_and_sizes(self, project, state):
        from deerx.pipeline.packaging import list_entries

        result = build_package(state, project.workspace, project.deliveries_dir)
        entries = list_entries(result.path)
        assert {e["name"] for e in entries} >= {f"{project.workspace.name}/TESLIMAT.md"}
        assert all(isinstance(e["bytes"], int) for e in entries)


class TestForceIsNotInherited:
    """`--force` 'fazi tekrar kos' demek; teslimat kapisini acmamali."""

    def _orchestrator(self, settings):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        return Orchestrator(settings, events=EventLog(None, echo=False), stream=False)

    def test_run_force_does_not_bypass_the_readiness_gate(self, settings):
        with self._orchestrator(settings) as orch:
            report = orch.run([Phase.PACKAGE], force=True)
            result = report.phases[0]
            assert result.status == Status.BLOCKED
            assert not list(settings.deliveries_dir.glob("*.zip"))

    def test_package_force_opens_the_gate(self, settings):
        (settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        with self._orchestrator(settings) as orch:
            report = orch.run([Phase.PACKAGE], package_force=True)
            assert report.phases[0].status == Status.DONE
            assert list(settings.deliveries_dir.glob("*.zip"))
