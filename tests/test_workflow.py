"""Is akisi katmani.

Her gelistirme bir is akisi; kosular onun adimlari; fazlar adimin icinde.
Once yalnizca kosular vardi ve hangi kosunun hangi gelistirmeye ait oldugu
hicbir yerde yazmiyordu -- yirmi kosuluk bir listede is akisi kullanicinin
kafasindaydi.
"""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from deerx.pipeline.models import Phase, Status
from deerx.web.app import STATIC_DIR, build_app
from deerx.web.runner import workflow_detail, workflow_list


def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def client(settings):
    with TestClient(build_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def app_state(client):
    return client.app.state.deerx


class TestWorkflowIdentity:
    """Is akisi kimligi HEDEF kimligidir.

    Ayni hedefle baslatilan kosular ayni cabanin adimlaridir; hedef
    degisince yeni bir is akisi acilir. Bu, faz tamamlanmasinin hedefe
    baglanmasiyla ayni kural ve ikisi birbirini tutuyor.
    """

    def test_same_goal_keeps_one_workflow(self, state):
        first = state.workflow_for_goal("Hesap makinesi")
        second = state.workflow_for_goal("Hesap makinesi")
        assert first["id"] == second["id"]

    @pytest.mark.parametrize("variant", [
        "  Hesap makinesi ", "hesap MAKINESI", "Hesap   makinesi",
    ])
    def test_cosmetic_differences_are_the_same_goal(self, state, variant):
        first = state.workflow_for_goal("Hesap makinesi")
        assert state.workflow_for_goal(variant)["id"] == first["id"]

    def test_new_goal_opens_a_new_workflow(self, state):
        first = state.workflow_for_goal("Saha servis sistemi")
        second = state.workflow_for_goal("Hesap makinesi")
        assert first["id"] != second["id"]
        assert second["seq"] == first["seq"] + 1

    def test_numbers_are_sequential_and_visible(self, state):
        seqs = [state.workflow_for_goal(f"Proje {i}")["seq"] for i in range(3)]
        assert seqs == [1, 2, 3]

    def test_brief_follows_the_goal(self, state):
        state.workflow_for_goal("Hesap makinesi", brief="ilk talimat")
        updated = state.workflow_for_goal("Hesap makinesi", brief="yeni talimat")
        assert updated["brief"] == "yeni talimat"


class TestRunsAreSteps:
    def test_a_run_joins_the_workflow_of_its_goal(self, orch_factory):
        with orch_factory() as orch:
            orch.run([Phase.INGEST], goal="Hesap makinesi")
            workflows = orch.state.list_workflows()
            assert len(workflows) == 1
            steps = orch.state.workflow_runs(workflows[0]["id"])
            assert len(steps) == 1

    def test_further_runs_become_further_steps(self, orch_factory):
        with orch_factory() as orch:
            orch.run([Phase.INGEST], goal="Hesap makinesi")
            orch.run([Phase.INGEST], goal="Hesap makinesi")
            workflow = orch.state.list_workflows()[0]
            assert len(orch.state.workflow_runs(workflow["id"])) == 2

    def test_changing_the_goal_starts_a_new_workflow(self, orch_factory):
        with orch_factory() as orch:
            orch.run([Phase.INGEST], goal="Saha servis")
            orch.run([Phase.INGEST], goal="Hesap makinesi")
            assert len(orch.state.list_workflows()) == 2

    def test_steps_are_ordered_by_run_number(self, orch_factory):
        with orch_factory() as orch:
            for _ in range(3):
                orch.run([Phase.INGEST], goal="Hesap makinesi")
            workflow = orch.state.list_workflows()[0]
            seqs = [r["seq"] for r in orch.state.workflow_runs(workflow["id"])]
            assert seqs == sorted(seqs)


class TestAdoptionMigration:
    """Bu sutundan onceki kosular da bir is akisina ait olmali."""

    def test_existing_runs_are_grouped_by_goal(self, settings):
        import sqlite3

        from deerx.pipeline.state import ProjectState

        db = settings.db_path
        db.parent.mkdir(parents=True, exist_ok=True)
        first = ProjectState(db)
        for seq, goal in enumerate(["A projesi", "A projesi", "B projesi"], start=1):
            first._conn.execute(
                "INSERT INTO runs (id, seq, goal, status, started_at) "
                "VALUES (?, ?, ?, 'done', 0)", (f"r{seq}", seq, goal),
            )
        first._conn.execute("UPDATE runs SET workflow_id = ''")
        first._conn.commit()
        first.close()

        # Yeniden acilinca gecis calisir.
        state = ProjectState(db)
        try:
            workflows = state.list_workflows()
            assert len(workflows) == 2, [w["goal"] for w in workflows]
            counts = sorted(len(state.workflow_runs(w["id"])) for w in workflows)
            assert counts == [1, 2]
        finally:
            state.close()

        # Ikinci acilis yeniden gruplamamali.
        again = ProjectState(db)
        try:
            assert len(again.list_workflows()) == 2
        finally:
            again.close()
        assert sqlite3  # kullanildi


class TestStepStates:
    """Adimin hali tek kelimeyle anlatilabilmeli."""

    def _detail(self, app_state, workflow_id):
        return workflow_detail(app_state.runner, app_state.orchestrator.state, workflow_id)

    def test_pending_approval_beats_running(self, app_state, monkeypatch):
        """Kayitta 'calisiyor' yazar ama gercekte sizi bekler.

        "Calisiyor" demek orada yanlis bilgi verir: kullanici bekler,
        sistem de onu bekler ve hicbir sey ilerlemez.
        """
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-a", goal="Hesap makinesi", workflow_id=workflow["id"])

        monkeypatch.setattr(app_state.runner, "status", lambda: {
            "running": True, "current": {"id": "run-a"},
            "pending_approvals": [{"id": "a1", "action": "Dosya yaz", "detail": "x"}],
        })
        step = self._detail(app_state, workflow["id"])["steps"][0]
        assert step["state"] == "needs_approval"
        assert step["approvals"][0]["id"] == "a1"

    def test_blocking_questions_show_as_needs_input(self, app_state, monkeypatch):
        from deerx.pipeline.models import Question

        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-b", goal="Hesap makinesi", workflow_id=workflow["id"])
        project.add_question(Question(key="Q-001", question="Hangi platform?", blocking=True))

        monkeypatch.setattr(app_state.runner, "status", lambda: {
            "running": True, "current": {"id": "run-b"}, "pending_approvals": [],
        })
        step = self._detail(app_state, workflow["id"])["steps"][0]
        assert step["state"] == "needs_input"
        assert step["questions"] == ["Q-001"]

    def test_record_says_running_but_nothing_is(self, app_state):
        """Surec yok ama kayit 'calisiyor' diyorsa adim yarida kalmistir."""
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-c", goal="Hesap makinesi", workflow_id=workflow["id"])
        step = self._detail(app_state, workflow["id"])["steps"][0]
        assert step["state"] == "stalled"

    def test_finished_steps_report_their_status(self, app_state):
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-d", goal="Hesap makinesi", workflow_id=workflow["id"])
        project.finish_run("run-d", status=Status.DONE)
        step = self._detail(app_state, workflow["id"])["steps"][0]
        assert step["state"] == Status.DONE


class TestWorkflowStateIsDerived:
    """`workflows.status` sutununa BAKILMAZ.

    Onu guncel tutan bir sey yok; saklanip guncellenmeyen bir durum sorulan
    soruya yanlis cevap verir. Butun adimlari bitmis bir is akisi
    "calisiyor" gorunuyordu.
    """

    def test_finished_steps_make_a_finished_workflow(self, app_state):
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-e", goal="Hesap makinesi", workflow_id=workflow["id"])
        project.finish_run("run-e", status=Status.DONE)

        assert project.get_workflow(workflow["id"])["status"] == Status.RUNNING, \
            "sutun hala eski degeri tasiyor"
        row = workflow_list(app_state.runner, project)["workflows"][0]
        assert row["state"] == Status.DONE, "gosterilen hal adimlardan gelmeli"

    def test_a_waiting_step_surfaces_on_the_workflow(self, app_state, monkeypatch):
        """Onay bekleyen bir adim, listede is akisi seviyesinde gorunmeli."""
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("run-f", goal="Hesap makinesi", workflow_id=workflow["id"])
        monkeypatch.setattr(app_state.runner, "status", lambda: {
            "running": True, "current": {"id": "run-f"},
            "pending_approvals": [{"id": "a1", "action": "x", "detail": ""}],
        })
        row = workflow_list(app_state.runner, project)["workflows"][0]
        assert row["state"] == "needs_approval"

    def test_empty_workflow_is_pending(self, app_state):
        project = app_state.orchestrator.state
        project.workflow_for_goal("Hic adimi olmayan")
        row = workflow_list(app_state.runner, project)["workflows"][0]
        assert row["state"] == Status.PENDING


class TestWorkflowRoutes:
    def test_list(self, client, app_state):
        app_state.orchestrator.state.workflow_for_goal("Hesap makinesi")
        data = client.get("/api/workflows").json()
        assert len(data["workflows"]) == 1

    def test_detail_by_sequence_number(self, client, app_state):
        app_state.orchestrator.state.workflow_for_goal("Hesap makinesi")
        data = client.get("/api/workflows/%231").json()
        assert data["workflow"]["seq"] == 1

    def test_unknown_workflow_is_a_404(self, client):
        assert client.get("/api/workflows/%2399").status_code == 404
        assert client.get("/api/workflows/yok").status_code == 404


class TestWorkflowInterface:
    """Arayuz uc seviyeli ve bekleyen adim kendini anlatmali."""

    def test_three_levels_exist(self):
        js = _asset("app.js")
        for fn in ("loadWorkflowList", "loadWorkflowDetail", "loadRunDetail"):
            assert f"function {fn}(" in js, fn

    def test_waiting_step_shows_its_buttons(self):
        """Onay yalnizca ustte acilan bir pencerede kalmamali.

        Pencere kapatildiginda is akisi "calisiyor" gibi durup hicbir sey
        ilerlemiyordu ve nedeni hicbir yerde yazmiyordu.
        """
        js = _asset("app.js")
        gate = js[js.index("function renderStepGate("):]
        gate = gate[:gate.index("\nfunction ")]
        assert "data-approve" in gate and "data-reject" in gate
        assert "needs_input" in gate
        assert "stalled" in gate

    def test_gate_buttons_are_wired(self):
        js = _asset("app.js")
        block = js[js.index("function bindStepGates("):]
        block = block[:block.index("\n// ")]
        assert "/api/approvals/" in block
        assert "stopPropagation" in block, "kapiya tiklamak adimi acmamali"

    def test_navigation_says_workflow(self):
        i18n = _asset("i18n.js")
        assert re.search(r'"nav\.workflow":\s*"İş akışı"', i18n)
        assert re.search(r'"nav\.workflow":\s*"Workflows"', i18n)


class TestNumericIds:
    """Tamamen rakamdan olusan kimlikler.

    Kimlikler `uuid4().hex[:12]`: on iki onaltilik karakter. Binde uc-dordu
    tamamen rakamdan olusuyor (or. `387341249535`). Adres hem kimligi hem
    `#3` gibi sira numarasini kabul ettigi icin, once numaraya bakan bir
    kontrol boyle bir kaydi var olmayan bir sira numarasi saniyor ve 404
    donuyordu -- kayit yerinde durdugu halde kullanici ona hicbir zaman
    ulasamiyordu. Testlerde kararsiz bir hata olarak ortaya cikti.
    """

    NUMERIC = "387341249535"

    def test_numeric_run_id_is_reachable(self, client, app_state):
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run(self.NUMERIC, goal="Hesap makinesi", workflow_id=workflow["id"])
        response = client.get(f"/api/runs/{self.NUMERIC}")
        assert response.status_code == 200, response.json()
        assert response.json()["run"]["id"] == self.NUMERIC

    def test_numeric_workflow_id_is_reachable(self, client, app_state):
        project = app_state.orchestrator.state
        project.create_workflow("Hesap makinesi")
        project._conn.execute(
            "UPDATE workflows SET id = ? WHERE seq = 1", (self.NUMERIC,)
        )
        project._conn.commit()
        response = client.get(f"/api/workflows/{self.NUMERIC}")
        assert response.status_code == 200, response.json()
        assert response.json()["workflow"]["id"] == self.NUMERIC

    def test_sequence_lookup_still_works(self, client, app_state):
        """`#1` kisayolu korunmali: kimlik once bakilir, numara sonra."""
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("abcdef123456", goal="Hesap makinesi", workflow_id=workflow["id"])
        assert client.get("/api/workflows/%231").json()["workflow"]["seq"] == 1
        assert client.get("/api/runs/%231").json()["run"]["seq"] == 1

    def test_an_id_wins_over_a_matching_sequence_number(self, client, app_state):
        """Kimlik "1" olan bir kayit, 1. sirali kayitla karistirilmamali."""
        project = app_state.orchestrator.state
        workflow = project.workflow_for_goal("Hesap makinesi")
        project.start_run("first-run", goal="Hesap makinesi", workflow_id=workflow["id"])
        project.start_run("1", goal="Hesap makinesi", workflow_id=workflow["id"])
        assert client.get("/api/runs/1").json()["run"]["id"] == "1"
        assert client.get("/api/runs/%231").json()["run"]["id"] == "first-run"

    def test_unknown_id_is_still_a_404(self, client):
        assert client.get("/api/runs/999999999999").status_code == 404
        assert client.get("/api/workflows/999999999999").status_code == 404
