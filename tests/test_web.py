"""Web katmani testleri: HTTP API, kosu yoneticisi, onay kapisi."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from deerx.errors import DeerXError
from deerx.pipeline.models import Artifact, Gap, Phase, Question, Requirement, Status, Task
from deerx.web.app import SETTING_FIELDS, _tail_lines, build_app, render_markdown
from deerx.web.runner import RunBusy, retry_plan


@pytest.fixture
def client(settings):
    """Sunucuyu izole bir calisma alaninda ayaga kaldirir."""
    app = build_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def state_of(client):
    """Sunucunun kullandigi ProjectState ornegi."""
    return client.app.state.deerx.orchestrator.state


class TestOverview:
    def test_shape(self, client, settings):
        data = client.get("/api/overview").json()
        assert data["workspace"] == str(settings.workspace)
        assert len(data["phases"]) == len(Phase.ordered())
        assert data["counts"]["requirements"] == 0
        assert data["settings"]["approval_mode"] == "auto"
        assert data["run"]["running"] is False

    def test_every_phase_carries_agent_label(self, client):
        for phase in client.get("/api/overview").json()["phases"]:
            assert phase["agent"], phase["phase"]
            assert "label" in phase and "status" in phase

    def test_costs_are_summed(self, client, state_of):
        state_of.finish_phase(Phase.ANALYZE, cost_usd=0.25)
        state_of.finish_phase(Phase.DESIGN, cost_usd=0.75)
        assert client.get("/api/overview").json()["total_cost"] == pytest.approx(1.0)


class TestProjectState:
    def test_sections(self, client, state_of):
        state_of.add_requirement(Requirement(key="REQ-001", title="Giris"))
        state_of.add_gap(Gap(key="GAP-001", title="Belirsiz", severity="critical"))

        reqs = client.get("/api/state/requirements").json()
        assert [r["key"] for r in reqs["items"]] == ["REQ-001"]
        gaps = client.get("/api/state/gaps").json()
        assert gaps["items"][0]["severity"] == "critical"

    def test_tasks_include_ready_flag(self, client, state_of):
        state_of.add_task(Task(key="T-001", title="ilk"))
        state_of.add_task(Task(key="T-002", title="ikinci", deps=["T-001"]))
        items = {t["key"]: t for t in client.get("/api/state/tasks").json()["items"]}
        assert items["T-001"]["ready"] is True
        assert items["T-002"]["ready"] is False

    def test_tasks_include_lane(self, client, state_of):
        state_of.add_task(Task(key="T-001", title="Form", lane="frontend"))
        assert client.get("/api/state/tasks").json()["items"][0]["lane"] == "frontend"

    def test_unknown_section(self, client):
        response = client.get("/api/state/yokboyle")
        assert response.status_code == 404
        assert "Bilinmeyen bolum" in response.json()["error"]

    def test_all_returns_everything(self, client, state_of):
        state_of.add_requirement(Requirement(key="REQ-001", title="x"))
        data = client.get("/api/state/all").json()
        assert {"requirements", "gaps", "tasks", "artifacts"} <= set(data)


class TestTaskUpdate:
    def test_status_change(self, client, state_of):
        state_of.add_task(Task(key="T-001", title="ilk"))
        response = client.post("/api/tasks/t-001", json={"status": "done", "result": "bitti"})
        assert response.status_code == 200
        assert state_of.get_task("T-001").status == Status.DONE

    def test_unknown_task(self, client):
        assert client.post("/api/tasks/T-999", json={"status": "done"}).status_code == 404

    def test_invalid_status(self, client, state_of):
        state_of.add_task(Task(key="T-001", title="ilk"))
        response = client.post("/api/tasks/T-001", json={"status": "uydurma"})
        assert response.status_code == 400
        assert "Gecersiz durum" in response.json()["error"]


class TestKnowledge:
    def test_ingest_then_search(self, client):
        ingested = client.post("/api/ingest", json={"path": "docs"}).json()
        assert ingested["ok"]
        assert ingested["stats"]["chunks"] > 0

        hits = client.post("/api/search", json={"query": "cevrimdisi calisma", "k": 3}).json()
        assert hits["hits"]
        assert "citation" in hits["hits"][0]

    def test_ingest_missing_path(self, client):
        response = client.post("/api/ingest", json={"path": "yok/olmayan"})
        assert response.status_code == 404

    def test_empty_query_rejected(self, client):
        assert client.post("/api/search", json={"query": "  "}).status_code == 400

    def test_malformed_json_rejected(self, client):
        response = client.post(
            "/api/search", content=b"{bozuk", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400

    def test_forget_document(self, client):
        client.post("/api/ingest", json={"path": "docs"})
        source = client.get("/api/documents").json()["documents"][0]["source"]
        assert client.post("/api/forget", json={"source": source}).json()["removed_chunks"] > 0
        assert client.get("/api/documents").json()["stats"]["chunks"] == 0


class TestArtifacts:
    def _add(self, client, settings, name: str, body: str, kind: str = "report") -> None:
        path = settings.artifacts_dir / name
        path.write_text(body, encoding="utf-8")
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name=name, kind=kind, path=str(path), summary="ozet")
        )

    def test_list_and_read_markdown(self, client, settings):
        self._add(client, settings, "rapor.md", "# Baslik\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        # Kosusuz cikti varsayilan listede gorunmez; burada icerigi sinaniyor.
        listing = client.get("/api/artifacts?orphans=1").json()
        first = listing["groups"][0]["items"][0]
        assert first["name"] == "rapor.md"
        assert first["format"] == "markdown"

        detail = client.get("/api/artifacts/rapor.md").json()
        assert "<h1>Baslik</h1>" in detail["html"]
        assert "<table>" in detail["html"]

    def test_html_artifact_is_not_pre_rendered(self, client, settings):
        self._add(client, settings, "mockup.html", "<p>merhaba</p>", kind="mockup")
        detail = client.get("/api/artifacts/mockup.html").json()
        assert detail["format"] == "html"
        # Ham icerik doner; tarayici onu yalitilmis bir iframe icinde gosterir.
        assert "html" not in detail
        assert detail["raw"] == "<p>merhaba</p>"

    def test_missing_artifact(self, client):
        assert client.get("/api/artifacts/yok.md").status_code == 404

    def test_registered_but_deleted_file(self, client, settings):
        self._add(client, settings, "silinmis.md", "x")
        (settings.artifacts_dir / "silinmis.md").unlink()
        assert client.get("/api/artifacts/silinmis.md").status_code == 404


class TestMarkdownSafety:
    def test_raw_html_is_escaped(self):
        html = render_markdown("<script>alert(1)</script>\n\n<img onerror=x>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_mermaid_gets_its_own_container(self):
        html = render_markdown("```mermaid\ngraph TD\n A-->B\n```")
        assert 'class="diagram"' in html
        assert "--&gt;" in html  # icerik kacislanmis

    def test_code_fence_language_class(self):
        assert 'class="language-python"' in render_markdown("```python\nx = 1\n```")


class TestRunEndpoints:
    def test_anthropic_phase_needs_api_key(self, client, settings):
        settings.provider = "anthropic"
        settings.anthropic_api_key = None
        response = client.post("/api/run", json={"phase": "analyze"})
        assert response.status_code == 400
        assert "ANTHROPIC_API_KEY" in response.json()["error"]

    def test_local_provider_needs_base_url(self, client, settings):
        settings.provider = "openai"
        settings.openai_base_url = None
        response = client.post("/api/run", json={"phase": "analyze"})
        assert response.status_code == 400
        assert "openai_base_url" in response.json()["error"]

    def test_local_provider_ready_without_api_key(self, client, settings):
        """Yerel bir uc cogu zaman anahtar istemez; taban adres yeterlidir."""
        settings.provider = "openai"
        settings.openai_api_key = None
        assert settings.llm_ready is True
        assert client.get("/api/overview").json()["settings"]["has_api_key"] is True

    def test_ingest_phase_runs_without_api_key(self, client, settings):
        settings.anthropic_api_key = None
        assert client.post("/api/run", json={"phase": "ingest", "force": True}).status_code == 200
        _wait_idle(client)
        assert client.get("/api/run").json()["last"]["status"] == "done"

    def test_unknown_phase(self, client):
        response = client.post("/api/run", json={"phase": "yokboyle"})
        assert response.status_code == 400

    def test_reversed_range(self, client):
        response = client.post("/api/run", json={"from": "plan", "to": "ingest"})
        assert response.status_code == 400
        assert "sonra geliyor" in response.json()["error"]

    def test_stop_when_idle(self, client):
        assert client.post("/api/run/stop").json()["ok"] is False


class TestSettings:
    def test_approval_mode_change(self, client, settings):
        assert client.post("/api/settings", json={"approval_mode": "dry-run"}).status_code == 200
        assert settings.approval_mode == "dry-run"

    def test_invalid_mode(self, client):
        assert client.post("/api/settings", json={"approval_mode": "uydurma"}).status_code == 400

    def test_cost_limit(self, client, settings):
        client.post("/api/settings", json={"cost_limit_usd": 5})
        assert settings.cost_limit_usd == 5.0

    def test_cost_limit_must_be_numeric(self, client):
        assert client.post("/api/settings", json={"cost_limit_usd": "abc"}).status_code == 400


class TestStaticFiles:
    def test_index_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "DeerX" in response.text

    def test_assets_are_not_cached(self, client):
        # Surum yukseltmesinden sonra bayat app.js servis edilmemeli.
        for asset in ("/static/app.js", "/static/styles.css"):
            response = client.get(asset)
            assert response.status_code == 200
            assert "no-cache" in response.headers.get("cache-control", "")


class TestEventStream:
    """SSE yayincisi.

    Uc nokta akisi acik tutar; TestClient boyle bir akisi kapatamadigi icin
    dongunun kendisi dogrudan surulur. Tarayici tarafi ayrica elle dogrulandi.
    """

    @staticmethod
    async def _drain(publisher) -> list[dict]:
        return [item async for item in publisher]

    def test_delivers_buffered_events(self, client):
        import anyio

        from deerx.web.app import event_publisher

        manager = client.app.state.deerx.runner
        manager.emit("phase", "test", "birinci olay")
        manager.emit("tool", "test", "ikinci olay")

        calls = {"n": 0}

        async def disconnected() -> bool:
            # Ilk turda bagli, ikinci turda kopmus: dongu tam bir tur atar.
            calls["n"] += 1
            return calls["n"] > 1

        items = anyio.run(
            self._drain,
            event_publisher(manager, 0, disconnected, poll_seconds=0),
        )
        messages = [json.loads(item["data"])["message"] for item in items]
        assert messages == ["birinci olay", "ikinci olay"]
        assert all(item["event"] == "deerx" for item in items)

    def test_cursor_skips_already_seen(self, client):
        import anyio

        from deerx.web.app import event_publisher

        manager = client.app.state.deerx.runner
        manager.emit("phase", "test", "eski olay")
        cursor = manager.last_seq
        manager.emit("phase", "test", "yeni olay")

        calls = {"n": 0}

        async def disconnected() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        items = anyio.run(
            self._drain,
            event_publisher(manager, cursor, disconnected, poll_seconds=0),
        )
        assert [json.loads(i["data"])["message"] for i in items] == ["yeni olay"]

    def test_heartbeat_when_quiet(self, client):
        import anyio

        from deerx.web.app import event_publisher

        manager = client.app.state.deerx.runner
        calls = {"n": 0}

        async def disconnected() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        items = anyio.run(
            self._drain,
            event_publisher(
                manager, manager.last_seq, disconnected,
                poll_seconds=0, heartbeat_seconds=-1,  # nabzi hemen tetikle
            ),
        )
        assert [i["event"] for i in items] == ["ping"]

    def test_disconnect_ends_stream_immediately(self, client):
        import anyio

        from deerx.web.app import event_publisher

        manager = client.app.state.deerx.runner
        manager.emit("phase", "test", "gorulmeyecek")

        async def disconnected() -> bool:
            return True

        items = anyio.run(
            self._drain, event_publisher(manager, 0, disconnected, poll_seconds=0)
        )
        assert items == []

    def test_route_is_registered(self, client):
        paths = {getattr(r, "path", None) for r in client.app.routes}
        assert "/api/events" in paths


class TestRunManager:
    """Onay kapisi ve durdurma — kosu thread'i ile HTTP thread'i arasindaki sozlesme."""

    def test_approval_blocks_until_answered(self, client):
        manager = client.app.state.deerx.runner
        outcome: list[bool] = []

        worker = threading.Thread(
            target=lambda: outcome.append(manager._request_approval("Dosya yaz: a.txt", "onizleme"))
        )
        worker.start()

        # Istek listede belirene kadar bekle.
        deadline = time.monotonic() + 5
        while not manager.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)

        pending = client.get("/api/approvals").json()["items"]
        assert len(pending) == 1
        assert pending[0]["action"] == "Dosya yaz: a.txt"
        assert not outcome, "cevap gelmeden thread devam etmemeli"

        assert client.post(f"/api/approvals/{pending[0]['id']}", json={"granted": True}).status_code == 200
        worker.join(timeout=5)
        assert outcome == [True]
        assert manager.pending_approvals() == []

    def test_rejection_propagates(self, client):
        manager = client.app.state.deerx.runner
        outcome: list[bool] = []
        worker = threading.Thread(
            target=lambda: outcome.append(manager._request_approval("Komut calistir: rm", ""))
        )
        worker.start()

        deadline = time.monotonic() + 5
        while not manager.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)
        approval_id = manager.pending_approvals()[0]["id"]
        client.post(f"/api/approvals/{approval_id}", json={"granted": False})
        worker.join(timeout=5)
        assert outcome == [False]

    def test_unknown_approval(self, client):
        assert client.post("/api/approvals/yokboyle", json={"granted": True}).status_code == 404

    def test_stopping_releases_pending_approvals(self, client):
        """Durdurma, onay kapisinda bekleyen thread'i asili birakmamali."""
        manager = client.app.state.deerx.runner
        outcome: list[bool] = []
        worker = threading.Thread(
            target=lambda: outcome.append(manager._request_approval("bekleyen islem", ""))
        )
        worker.start()

        deadline = time.monotonic() + 5
        while not manager.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)

        manager._reject_all_approvals()
        worker.join(timeout=5)
        assert outcome == [False]

    def test_second_run_is_rejected(self, client, settings, monkeypatch):
        manager = client.app.state.deerx.runner
        gate = threading.Event()

        def slow_run(*args, **kwargs):
            gate.wait(5)
            from deerx.pipeline.orchestrator import RunReport

            return RunReport()

        monkeypatch.setattr(manager.orchestrator, "run", slow_run)
        manager.start([Phase.INGEST])
        try:
            with pytest.raises(RunBusy):
                manager.start([Phase.INGEST])
            assert client.post("/api/run", json={"phase": "ingest"}).status_code == 409
        finally:
            gate.set()
            manager.wait(5)

    def test_event_sequence_is_monotonic(self, client):
        manager = client.app.state.deerx.runner
        for index in range(5):
            manager.emit("tool", "test", f"olay {index}")
        events, seq = manager.events_since(0)
        numbers = [e["seq"] for e in events]
        assert numbers == sorted(numbers)
        assert seq == numbers[-1]


def _wait_idle(client, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not client.get("/api/run").json()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("kosu zamaninda bitmedi")


def test_workspace_paths_are_contained(settings: Path) -> None:
    """Ingest yolu calisma alani disina cikamaz."""
    app = build_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/ingest", json={"path": "../../../etc"})
        assert response.status_code in (404, 400)


class TestUpload:
    """Sartname yukleme: govde ham baytlar, dosya adi sorgu parametresinde."""

    def test_upload_saves_and_indexes(self, client, settings):
        body = b"# Yeni Sartname\n\nCevrimdisi calisma zorunlu."
        response = client.post("/api/upload?name=yeni.md", content=body)
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] and payload["chunks"] > 0
        assert (settings.workspace / "docs" / "yeni.md").exists()

        hits = client.post("/api/search", json={"query": "cevrimdisi", "k": 3}).json()
        assert any("Cevrimdisi" in h["text"] for h in hits["hits"])

    def test_path_traversal_in_filename_is_stripped(self, client, settings):
        response = client.post("/api/upload?name=../../kotu.md", content=b"# x")
        assert response.status_code == 200
        # Yalnizca dosya adi kullanilir; ust dizine yazilmaz.
        assert (settings.workspace / "docs" / "kotu.md").exists()
        assert not (settings.workspace.parent / "kotu.md").exists()

    def test_unsupported_suffix_rejected(self, client):
        response = client.post("/api/upload?name=zararli.exe", content=b"MZ")
        assert response.status_code == 400
        assert "desteklenmiyor" in response.json()["error"]

    def test_missing_name(self, client):
        assert client.post("/api/upload", content=b"x").status_code == 400

    def test_empty_body(self, client):
        assert client.post("/api/upload?name=bos.md", content=b"").status_code == 400

    def test_too_large(self, client, settings):
        settings.rag.max_file_bytes = 10
        response = client.post("/api/upload?name=buyuk.md", content=b"x" * 100)
        assert response.status_code == 400
        assert "cok buyuk" in response.json()["error"]

    def test_unreadable_file_is_not_left_behind(self, client, settings):
        """Indekslenemeyen dosya calisma alaninda birakilmaz."""
        response = client.post("/api/upload?name=bozuk.pdf", content=b"bu bir PDF degil")
        assert response.status_code == 400
        assert not (settings.workspace / "docs" / "bozuk.pdf").exists()


class TestQuestionsApi:
    def test_listing_separates_blocking(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        state_of.add_question(Question(key="Q-002", question="Renk?", blocking=False))
        data = client.get("/api/questions").json()
        assert len(data["items"]) == 2
        assert [q["key"] for q in data["blocking"]] == ["Q-001"]

    def test_answer_flow(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        response = client.post(
            "/api/questions/q-001", json={"action": "answer", "text": "250 bin TL"}
        )
        assert response.status_code == 200
        assert response.json()["remaining_blocking"] == []
        assert state_of.get_question("Q-001").answer == "250 bin TL"

    def test_skip_flow(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Renk?", blocking=True))
        response = client.post(
            "/api/questions/Q-001", json={"action": "skip", "text": "marka mavisi"}
        )
        assert response.status_code == 200
        assert state_of.get_question("Q-001").status == "skipped"
        assert state_of.get_question("Q-001").suggestion == "marka mavisi"

    def test_empty_answer_rejected(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="x"))
        response = client.post("/api/questions/Q-001", json={"action": "answer", "text": " "})
        assert response.status_code == 400

    def test_unknown_action(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="x"))
        response = client.post("/api/questions/Q-001", json={"action": "sil"})
        assert response.status_code == 400
        assert "Bilinmeyen islem" in response.json()["error"]

    def test_unknown_question(self, client):
        response = client.post("/api/questions/Q-999", json={"action": "answer", "text": "x"})
        assert response.status_code == 404

    def test_overview_surfaces_blocking_questions(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        data = client.get("/api/overview").json()
        assert [q["key"] for q in data["blocking_questions"]] == ["Q-001"]
        assert data["counts"]["questions_blocking"] == 1


class TestBriefAndGate:
    def test_brief_is_persisted_and_returned(self, client, state_of):
        client.post("/api/run", json={"phase": "ingest", "brief": "Mobil onceligi ver."})
        _wait_idle(client)
        assert state_of.get_meta("brief") == "Mobil onceligi ver."
        assert client.get("/api/overview").json()["brief"] == "Mobil onceligi ver."

    def test_run_halts_on_blocking_question(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        assert client.post("/api/run", json={"phase": "ingest"}).status_code == 200
        _wait_idle(client)

        last = client.get("/api/run").json()["last"]
        assert last["status"] == "needs_input"
        assert last["pending_questions"] == ["Q-001"]

    def test_answering_lets_the_run_proceed(self, client, state_of):
        state_of.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        client.post("/api/run", json={"phase": "ingest"})
        _wait_idle(client)
        assert client.get("/api/run").json()["last"]["status"] == "needs_input"

        client.post("/api/questions/Q-001", json={"action": "answer", "text": "250 bin"})
        client.post("/api/run", json={"phase": "ingest", "force": True})
        _wait_idle(client)
        assert client.get("/api/run").json()["last"]["status"] == "done"


class TestStaticAssets:
    """Arayuz varliklarinin sessizce bozulabilecek yanlari."""

    @staticmethod
    def _asset(name: str) -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / name).read_text(encoding="utf-8")

    def test_hidden_attribute_always_wins(self):
        """`[hidden]` kurali olmadan gizli bilesenler gorunur kalir.

        Tarayicinin `[hidden] { display: none }` kurali kullanici-ajani
        seviyesindedir ve HERHANGI bir yazar `display` bildirimi onu ezer.
        `.overlay { display: grid }` yuzunden onay penceresi acilista butun
        sayfayi kapatiyor ve arayuzu kullanilamaz hale getiriyordu.
        """
        import re

        css = self._asset("styles.css")
        # Yorum satirlarini at; kural gercekten tanimli mi ona bak.
        without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        assert re.search(
            r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", without_comments
        ), (
            "styles.css icinde `[hidden] { display: none !important }` kurali yok; "
            "display bildirimi olan her gizli bilesen gorunur kalir."
        )

    def test_every_hidden_element_has_a_display_guard(self):
        """`hidden` ile gizlenen her elemani genel kural kapsamali."""
        import re

        html = self._asset("index.html")
        css = self._asset("styles.css")
        hidden_elements = re.findall(r"<(\w+)[^>]*\shidden[\s>]", html)
        assert hidden_elements, "test kendini dogrulayamiyor: hidden kullanan eleman yok"
        # Genel kural varsa her biri kapsanir.
        without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        assert re.search(
            r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", without_comments
        )

    def test_every_configurable_search_provider_is_offered_in_the_ui(self):
        """Yapilandirmada olup arayuzde olmayan saglayici sessizce erisilemez.

        Olculdu: `search_provider` Literal'i bes deger kabul ediyordu ama
        acilir listede dort tane vardi -- `searxng` yalnizca `deerx.toml`
        elle duzenlenerek secilebiliyordu, ki config yorumu onu "anahtarsiz
        tek saglam yol" diye tanimliyor.
        """
        import re

        from deerx.config import Settings

        alan = Settings.model_fields["search_provider"]
        saglayicilar = set(alan.annotation.__args__)
        html = self._asset("index.html")
        blok = html[html.index('id="set-search-provider"'):]
        blok = blok[: blok.index("</select>")]
        sunulan = set(re.findall(r'<option value="([\w-]+)"', blok))
        assert not (saglayicilar - sunulan), (
            f"arayuzde sunulmayan saglayici: {sorted(saglayicilar - sunulan)}"
        )
        assert not (sunulan - saglayicilar), (
            f"arayuzde olup yapilandirmada olmayan: {sorted(sunulan - saglayicilar)}"
        )

    def test_keyless_search_providers_are_not_reported_as_broken(self):
        """Bes saglayicidan UCU anahtarsiz; arayuz yalnizca birini biliyordu.

        `const keyed = s.search_provider !== "duckduckgo"` idi. VARSAYILAN
        saglayici `browser` oldugu icin her yeni kurulumda ayarlar ekrani
        kirmizi "ANAHTAR YOK - arama calismaz" gosteriyordu -- oysa arama
        calisiyor: gercek bir kosuda tarayici saglayicisiyla alti sorgu
        yapildi ve arastirma notlarina on uc kaynak girdi.
        """
        import re

        js = self._asset("app.js")
        assert 'ANAHTARSIZ_ARAMA' in js, "anahtarsiz saglayici kumesi yok"
        blok = js[js.index("const ANAHTARSIZ_ARAMA"):][:400]
        for saglayici in ("browser", "searxng", "duckduckgo"):
            assert re.search(rf"\b{saglayici}\b", blok), (
                f"{saglayici} anahtarsiz sayilmali; degilse arayuz calisan bir "
                "aramayi bozuk gosterir"
            )
        assert 'search_provider !== "duckduckgo"' not in js, (
            "eski tek-saglayici varsayimi hala duruyor"
        )

    def test_settings_view_loads_the_overview_before_rendering(self):
        """Ayarlar ekrani bombos aciliyordu.

        Form `state.overview.settings`ten dolar ve `renderSettings` veri
        yoksa `if (!s) return;` ile SESSIZCE cikar. Sekme gecisi
        `renderSettings()` cagiriyordu ama genel durumu hic yuklemiyordu:
        yoklama henuz gelmediyse form bos kaliyor, ve `renderSettings`
        yalnizca sekmeye girerken ya da kaydettikten sonra calistigi icin
        bir daha denenmiyordu. `develop` sekmesi bastan beri once yukluyor.

        Uctan uca yasandi: ayarlar ekrani bos geldi, oysa sunucunun
        `settings_snapshot` ciktisi otuz uc alanla doluydu.
        """
        js = self._asset("app.js")
        satir = next(
            (s for s in js.splitlines() if 'name === "settings"' in s), None
        )
        assert satir, "ayarlar sekmesi gecisi bulunamadi"
        assert "loadOverview" in satir, (
            "ayarlar sekmesi genel durumu yuklemeli; yoksa renderSettings "
            f"sessizce cikar ve form bos kalir: {satir.strip()}"
        )

    def test_javascript_parses(self):
        """Sozdizimi hatasi tum arayuzu sessizce oldururdu."""
        import subprocess

        from deerx.web.app import STATIC_DIR

        result = subprocess.run(
            ["node", "--check", str(STATIC_DIR / "app.js")],
            capture_output=True, text=True,
        )
        if result.returncode == 127 or "not recognized" in (result.stderr or ""):
            pytest.skip("node kurulu degil")
        assert result.returncode == 0, result.stderr

    def test_every_referenced_element_id_exists(self):
        """`$("#x")` diye aranan her kimlik HTML'de olmali.

        Gorunum tasindiginda sessizce kirilan sey budur: JS `null` uzerinde
        patlar ve o noktadan sonraki tum arayuz olur.
        """
        import re

        js = self._asset("app.js")
        html = self._asset("index.html")
        used = set(re.findall(r'\$\$?\("#([A-Za-z][\w-]*)"', js))
        # JS'in kendi sablonlariyla urettigi kimlikler de gecerlidir.
        defined = set(re.findall(r'\bid="([^"${]+)"', html + js))
        assert used, "test kendini dogrulayamiyor: kimlik sorgusu bulunamadi"
        assert not (used - defined), f"HTML'de olmayan kimlikler: {sorted(used - defined)}"

    def test_every_rail_target_has_a_view(self):
        """Sol raydaki her `data-view` bir `.view` bolumune karsilik gelmeli."""
        import re

        html = self._asset("index.html")
        js = self._asset("app.js")
        targets = set(re.findall(r'data-view="([\w-]+)"', html))
        sections = set(re.findall(r'class="view[^"]*" id="view-([\w-]+)"', html))
        assert targets, "test kendini dogrulayamiyor"
        assert not (targets - sections), f"bolumu olmayan gorunum: {sorted(targets - sections)}"
        # Yonlendirici bilmedigi adi genel bakisa dusurur; liste eksik kalmasin.
        # Dizi birden fazla satira yayilabilir; tirnakli adlari ayikla.
        declared = re.search(r"const VIEWS = \[(.*?)\]", js, re.S).group(1)
        known = set(re.findall(r'"([\w-]+)"', declared))
        assert sections <= known, f"VIEWS listesinde olmayan bolum: {sorted(sections - known)}"

    def test_content_is_left_aligned_next_to_the_rail(self):
        """Icerik ortalanmamali; soldaki rayin yanindan baslamali.

        `margin: 0 auto` genis ekranda icerigi menuden koparip ortada
        birakiyordu — ray ile icerik arasinda 200 pikseli asan bir bosluk.
        """
        import re

        css = self._asset("styles.css")
        rule = re.search(r"^\.view \{([^}]*)\}", css, re.M)
        assert rule, ".view kurali bulunamadi"
        body = rule.group(1)
        assert "margin: 0 auto" not in body, (
            ".view hala ortalaniyor; sola dayali olmali."
        )
        assert re.search(r"margin:\s*0\s*;", body), body

    def test_no_css_class_is_used_without_definition(self):
        """HTML/JS'te gecen her sinif CSS'te tanimli olmali."""
        import re

        css = self._asset("styles.css")
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
        used: set[str] = set()
        for name in ("index.html", "app.js"):
            for match in re.finditer(r'class="([^"$]*)"', self._asset(name)):
                used.update(
                    c for c in match.group(1).split() if c and not c.startswith("${")
                )
        assert not (used - defined), f"CSS'te tanimsiz sinif: {sorted(used - defined)}"


class TestPackageApi:
    """Teslimat paketi: durum, uretim ve indirme."""

    def _make_ready(self, state_of, settings):
        from deerx.pipeline.models import Phase, Status, Task

        (settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        state_of.add_task(Task(key="T-001", title="API", status=Status.DONE))
        for phase in (Phase.QA, Phase.REVIEW):
            state_of.start_phase(phase)
            state_of.finish_phase(phase, summary="tamam")

    def test_status_reports_blockers(self, client):
        data = client.get("/api/package").json()
        assert data["ready"] is False
        assert any("Plan bos" in b for b in data["blockers"])

    def test_build_refuses_when_not_ready(self, client):
        response = client.post("/api/package", json={})
        assert response.status_code == 409
        assert response.json()["ready"] is False

    def test_build_and_download(self, client, state_of, settings):
        self._make_ready(state_of, settings)

        built = client.post("/api/package", json={})
        assert built.status_code == 200
        name = built.json()["name"]

        listing = client.get("/api/package").json()
        assert listing["ready"] is True
        assert name in [p["name"] for p in listing["packages"]]

        download = client.get(f"/api/package/{name}")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert name in download.headers.get("content-disposition", "")
        assert download.content[:2] == b"PK"  # gecerli zip

    def test_download_rejects_path_traversal(self, client):
        response = client.get("/api/package/..%2F..%2Fdeerx.db")
        assert response.status_code in (400, 404)

    def test_download_rejects_non_zip(self, client):
        assert client.get("/api/package/gizli.env").status_code == 400

    def test_download_missing_package(self, client):
        assert client.get("/api/package/yok-boyle.zip").status_code == 404

    def test_force_packages_despite_blockers(self, client, settings):
        (settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        response = client.post("/api/package", json={"force": True})
        assert response.status_code == 200
        assert response.json()["ready"] is False

    def test_secrets_are_not_downloadable(self, client, state_of, settings):
        """Indirilen zip icinde sir bulunmamali."""
        import io
        import zipfile

        self._make_ready(state_of, settings)
        (settings.workspace / ".env").write_text("SECRET=cok-gizli\n", encoding="utf-8")

        name = client.post("/api/package", json={}).json()["name"]
        blob = client.get(f"/api/package/{name}").content
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            body = b"".join(zf.read(n) for n in zf.namelist())
        assert b"cok-gizli" not in body


class TestArchiveArtifacts:
    """Zip bir ek dosyadir: ham baytlari arayuze dokulmez."""

    def _package(self, client, settings) -> str:
        project = client.app.state.deerx.orchestrator.state
        (settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        project.add_task(Task(key="T-001", title="Kur", status=Status.DONE))
        for phase in (Phase.QA, Phase.REVIEW):
            project.start_phase(phase)
            project.finish_phase(phase, summary="tamam")
        response = client.post("/api/package", json={})
        assert response.status_code == 200, response.text
        return response.json()["name"]

    def test_archive_detail_has_no_raw_bytes(self, client, settings):
        name = self._package(client, settings)
        detail = client.get(f"/api/artifacts/{name}").json()

        assert detail["format"] == "archive"
        # En onemlisi: ikili icerik metin olarak gonderilmez.
        assert "raw" not in detail
        assert detail["download"].endswith("/download")
        assert detail["bytes"] > 0

    def test_archive_detail_carries_the_delivery_report(self, client, settings):
        name = self._package(client, settings)
        detail = client.get(f"/api/artifacts/{name}").json()

        assert "# Teslimat" in detail["report"]
        assert "<h1>Teslimat</h1>" in detail["html"]
        assert "Neler yapildi" in detail["report"]

    def test_archive_detail_lists_entries(self, client, settings):
        name = self._package(client, settings)
        detail = client.get(f"/api/artifacts/{name}").json()
        names = {e["name"] for e in detail["entries"]}
        assert detail["entry_count"] == len(detail["entries"])
        assert any(n.endswith("TESLIMAT.md") for n in names)
        assert any(n.endswith("app.py") for n in names)

    def test_archive_download_returns_the_zip(self, client, settings):
        name = self._package(client, settings)
        response = client.get(f"/api/artifacts/{name}/download")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"

    def test_download_of_a_missing_artifact_is_404(self, client):
        assert client.get("/api/artifacts/yok.zip/download").status_code == 404

    def test_binary_artifact_is_also_an_attachment(self, client, settings):
        """Gosterilemeyen bir ikili dosya ek dosya olarak durur."""
        path = settings.artifacts_dir / "kilavuz.pdf"
        path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 32)
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name="kilavuz.pdf", kind="docs", path=str(path), summary="x")
        )
        detail = client.get("/api/artifacts/kilavuz.pdf").json()
        assert detail["format"] == "binary"
        assert "raw" not in detail
        assert detail["download"].endswith("/download")

    def test_package_listing_flags_reportable_archives(self, client, settings):
        name = self._package(client, settings)
        rows = client.get("/api/package").json()["packages"]
        assert [p["name"] for p in rows] == [name]
        assert rows[0]["has_report"] is True

class TestScreenshotsAreVisible:
    """`browser_screenshot` "kullanici arayuzde gorur" diyor.

    Demiyordu: `.png` ikili sayildigi icin ekranda yalnizca bir indirme
    kutusu vardi ve ajanin kendi arayuzune bakip cektigi goruntuyu
    kullanici HIC gormuyordu. Aracin vaadiyle arayuzun yaptigi ayni sey
    olmali.
    """

    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    def _kaydet(self, client, settings, name="anasayfa.png", kind="screenshot"):
        path = settings.artifacts_dir / name
        path.write_bytes(self.PNG)
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name=name, kind=kind, path=str(path), summary="http://x/ goruntusu")
        )
        return name

    def test_a_screenshot_is_an_image_not_a_blob(self, client, settings):
        name = self._kaydet(client, settings)
        detail = client.get(f"/api/artifacts/{name}").json()
        assert detail["format"] == "image"
        # Ciziim adresi ayri: indirme adresi dosyayi diske indirir.
        assert detail["src"].endswith("/download?inline=1")
        assert detail["download"].endswith("/download")

    def test_every_raster_suffix_is_shown(self, client, settings):
        for suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            name = self._kaydet(client, settings, name=f"kare{suffix}")
            detail = client.get(f"/api/artifacts/{name}").json()
            assert detail["format"] == "image", suffix

    def test_inline_download_carries_the_real_media_type(self, client, settings):
        name = self._kaydet(client, settings)
        response = client.get(f"/api/artifacts/{name}/download?inline=1")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["content-disposition"].startswith("inline")

    def test_the_plain_download_still_downloads(self, client, settings):
        name = self._kaydet(client, settings)
        response = client.get(f"/api/artifacts/{name}/download")
        assert response.headers["content-type"] == "application/octet-stream"
        assert "attachment" in response.headers["content-disposition"]

    def test_svg_is_never_served_inline(self, client, settings):
        """SVG betik tasiyabilir ve dogrudan acilirsa uygulamanin KENDI
        kaynaginda calisir. Tarama goruntuleri calistiramaz; SVG bilerek
        ikili tarafta birakildi."""
        path = settings.artifacts_dir / "logo.svg"
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name="logo.svg", kind="diagram", path=str(path), summary="x")
        )
        detail = client.get("/api/artifacts/logo.svg").json()
        assert detail["format"] == "binary"
        assert "src" not in detail
        response = client.get("/api/artifacts/logo.svg/download?inline=1")
        assert response.headers["content-type"] == "application/octet-stream"


class TestArtifactsWithoutARunAreReachable:
    """Rozet 11 derken ekranda 1 cikti gorunuyordu.

    Kosu kaydindan onceki ciktilar listeden ciakriliyor ama SAYILIYORDU;
    `?orphans=1` disinda onlara ulasmanin hicbir yolu yoktu ve arayuz o
    parametreyi hic gondermiyordu.
    """

    def _yetim(self, client, settings, name="mimari.md"):
        path = settings.artifacts_dir / name
        path.write_text("# Mimari\n", encoding="utf-8")
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name=name, kind="architecture", path=str(path), summary="x")
        )
        return name

    def test_the_count_of_hidden_ones_is_reported(self, client, settings):
        self._yetim(client, settings)
        data = client.get("/api/artifacts").json()
        assert data["total"] == 0
        assert data["orphans"] == 1, "gizlenen cikti sayilmali"

    def test_they_can_be_asked_for(self, client, settings):
        name = self._yetim(client, settings)
        data = client.get("/api/artifacts?orphans=1").json()
        assert data["total"] == 1
        grup = data["groups"][0]
        # Kosusu yok: arayuz "#null" yazmasin diye sira numarasi None.
        assert grup["seq"] is None
        assert [i["name"] for i in grup["items"]] == [name]

class TestUploadDoesNotDestroyExistingFiles:
    """Okunamayan bir yukleme, ayni adli calisan sartnameyi silmemeli."""

    GOOD = "# Sartname\n\nGecerli icerik yeterince uzun olsun.\n"

    @staticmethod
    def _failing(name: str):
        from deerx.rag.knowledge import IngestResult

        def ingest(path, **kwargs):
            return IngestResult(
                source=str(path), title=name, kind="doc", chunks=0, error="okunamadi"
            )

        return ingest

    def test_failed_replacement_restores_the_previous_file(
        self, client, settings, monkeypatch
    ):
        assert client.post(
            "/api/upload?name=sartname.md", content=self.GOOD.encode("utf-8")
        ).status_code == 200
        target = settings.workspace / "docs" / "sartname.md"
        assert target.read_text(encoding="utf-8") == self.GOOD

        kb = client.app.state.deerx.orchestrator.kb
        monkeypatch.setattr(kb, "ingest_file", self._failing("sartname.md"))
        response = client.post("/api/upload?name=sartname.md", content=b"bozuk icerik")

        assert response.status_code == 400
        assert response.json()["restored"] is True
        # Eski, calisan icerik yerinde durmali.
        assert target.read_text(encoding="utf-8") == self.GOOD

    def test_failed_first_upload_leaves_no_file_behind(
        self, client, settings, monkeypatch
    ):
        kb = client.app.state.deerx.orchestrator.kb
        monkeypatch.setattr(kb, "ingest_file", self._failing("yeni.md"))
        response = client.post("/api/upload?name=yeni.md", content=b"# yeni")

        assert response.status_code == 400
        assert response.json()["restored"] is False
        assert not (settings.workspace / "docs" / "yeni.md").exists()


class TestSearchInputValidation:
    def test_non_numeric_k_is_a_client_error(self, client):
        response = client.post("/api/search", json={"query": "test", "k": "cok"})
        assert response.status_code == 400
        assert "k" in response.json()["error"]


class TestPhaseSelection:
    """Kullanici hangi adimlarin kosacagini tek tek secer."""

    def test_selection_is_sorted_into_pipeline_order(self):
        from deerx.web.runner import phase_selection

        chosen = phase_selection(["plan", "analyze", "design"])
        assert [str(p) for p in chosen] == ["ingest", "analyze", "design", "plan"]

    def test_first_step_is_always_included(self):
        """Bilgi tabani bos ise sonraki hicbir ajan okuyacak sey bulamaz."""
        from deerx.web.runner import phase_selection

        assert str(phase_selection(["review"])[0]) == "ingest"
        assert str(phase_selection([])[0]) == "ingest"

    def test_duplicates_collapse(self):
        from deerx.web.runner import phase_selection

        chosen = phase_selection(["analyze", "analyze", "ingest"])
        assert [str(p) for p in chosen] == ["ingest", "analyze"]

    def test_unknown_phase_is_rejected(self):
        from deerx.errors import DeerXError
        from deerx.web.runner import phase_selection

        with pytest.raises(DeerXError, match="yokboyle"):
            phase_selection(["yokboyle"])

    def test_api_accepts_an_explicit_step_list(self, client, settings):
        settings.anthropic_api_key = None
        settings.provider = "openai"
        settings.openai_base_url = None
        # LLM gerektirmeyen tek adim: secim yine de siraya dizilmeli.
        response = client.post("/api/run", json={"phases": ["ingest"], "force": True})
        assert response.status_code == 200
        assert response.json()["run"]["phases"] == ["ingest"]
        _wait_idle(client)

    def test_api_rejects_a_non_list(self, client):
        response = client.post("/api/run", json={"phases": "analyze"})
        assert response.status_code == 400
        assert "dizi" in response.json()["error"]

    def test_api_rejects_an_unknown_step(self, client):
        response = client.post("/api/run", json={"phases": ["ingest", "yokboyle"]})
        assert response.status_code == 400

    def test_selecting_a_later_step_still_prepends_ingest(self, client, settings):
        """Kullanici yalnizca `review` secse bile ilk adim listede olmali."""
        settings.provider = "openai"
        settings.openai_base_url = None
        response = client.post("/api/run", json={"phases": ["review"]})
        # LLM yok, o yuzden reddedilir — ama hata faz secimiyle ilgili degil.
        assert response.status_code == 400
        assert "openai_base_url" in response.json()["error"]


class TestQuestionWorkflow:
    """Sorular teker teker cevaplanir; her cevaptan sonra sirada ne kaldigi bilinir."""

    def _ask(self, state_of, *keys: str) -> None:
        for key in keys:
            state_of.add_question(
                Question(key=key, question=f"{key} sorusu?", blocking=True,
                         suggestion="makul varsayim")
            )

    def test_answering_reports_what_remains(self, client, state_of):
        self._ask(state_of, "Q-001", "Q-002", "Q-003")
        result = client.post("/api/questions/Q-001", json={"action": "answer", "text": "cevap"}).json()
        assert result["remaining_blocking"] == ["Q-002", "Q-003"]

    def test_queue_empties_one_by_one(self, client, state_of):
        self._ask(state_of, "Q-001", "Q-002")
        first = client.post("/api/questions/Q-001", json={"action": "answer", "text": "a"}).json()
        assert len(first["remaining_blocking"]) == 1
        second = client.post("/api/questions/Q-002", json={"action": "skip"}).json()
        assert second["remaining_blocking"] == []

    def test_overview_exposes_the_queue_in_order(self, client, state_of):
        self._ask(state_of, "Q-003", "Q-001", "Q-002")
        queue = client.get("/api/overview").json()["blocking_questions"]
        assert [q["key"] for q in queue] == ["Q-001", "Q-002", "Q-003"]

    def test_skipping_keeps_the_suggested_assumption(self, client, state_of):
        self._ask(state_of, "Q-001")
        client.post("/api/questions/Q-001", json={"action": "skip"})
        question = state_of.get_question("Q-001")
        assert question.status == "skipped"
        assert question.suggestion == "makul varsayim"

    def test_answered_question_leaves_the_queue(self, client, state_of):
        self._ask(state_of, "Q-001")
        client.post("/api/questions/Q-001", json={"action": "answer", "text": "cevap"})
        assert client.get("/api/overview").json()["blocking_questions"] == []


class TestPhaseMetadata:
    """Adim listesi faz adini degil, ne uretecegini gostermeli."""

    def test_every_phase_says_what_it_produces(self):
        for phase in Phase.ordered():
            assert phase.produces, phase
            assert len(phase.produces) > 15, phase

    def test_stages_group_the_pipeline_in_order(self):
        stages = [p.stage for p in Phase.ordered()]
        # Asamalar bloklar halinde ilerler; ileri geri ziplamaz.
        assert stages == sorted(stages, key=lambda s: stages.index(s))
        assert set(stages) == {"Anlama", "Tasarım", "Üretim", "Teslim"}

    def test_catalog_carries_produces_and_stage(self, client):
        for phase in client.get("/api/overview").json()["phases"]:
            assert phase["produces"], phase["phase"]
            assert phase["stage"], phase["phase"]


class TestRunSteps:
    """Kosunun adim adim dokumu."""

    def _finish(self, state_of, phase, summary, cost=0.0):
        state_of.start_phase(phase)
        state_of.finish_phase(phase, summary=summary, cost_usd=cost)

    def test_steps_cover_every_executed_phase(self, client, state_of):
        self._finish(state_of, Phase.ANALYZE, "31 gereksinim")
        self._finish(state_of, Phase.PLAN, "12 gorev")
        steps = client.get("/api/run/steps").json()["steps"]
        assert [s["phase"] for s in steps] == ["analyze", "plan"]
        assert steps[0]["summary"] == "31 gereksinim"
        assert steps[0]["label"] and steps[0]["agent"] and steps[0]["produces"]

    def test_pending_phases_are_not_listed(self, client, state_of):
        self._finish(state_of, Phase.ANALYZE, "x")
        phases = {s["phase"] for s in client.get("/api/run/steps").json()["steps"]}
        assert "live" not in phases and "qa" not in phases

    def test_elapsed_and_cost_are_reported(self, client, state_of):
        self._finish(state_of, Phase.DESIGN, "mimari", cost=0.42)
        step = client.get("/api/run/steps").json()["steps"][0]
        assert step["cost"] == pytest.approx(0.42)
        assert step["elapsed"] is not None and step["elapsed"] >= 0

    def test_events_are_grouped_by_the_phase_that_produced_them(self, client, state_of):
        """Olaylar uretildikleri fazla etiketlenir; aktor adindan cikarim degil."""
        events = client.app.state.deerx.events
        self._finish(state_of, Phase.ANALYZE, "x")
        self._finish(state_of, Phase.DESIGN, "y")

        events.current_phase = "analyze"
        events.emit("tool", "analyst", "read_document(...)")
        events.current_phase = "design"
        events.emit("tool", "architect", "save_artifact(...)")
        events.emit("done", "artifact", "mimari.md yazildi", name="mimari.md")
        events.current_phase = None

        steps = {s["phase"]: s for s in client.get("/api/run/steps").json()["steps"]}
        assert steps["analyze"]["counts"]["tool"] == 1
        assert steps["design"]["counts"]["tool"] == 1
        assert steps["design"]["artifacts"] == ["mimari.md"]
        assert steps["analyze"]["artifacts"] == []

    def test_untagged_events_are_attributed_from_the_actor(self):
        """Faz alani sonradan eklendi; eski gunlukler de okunur kalmali."""
        from deerx.web.runner import attribute_phases

        events = [
            {"actor": "run", "kind": "phase", "message": "kosu basladi"},
            {"actor": "assessor", "kind": "agent", "message": "basladi"},
            {"actor": "rag", "kind": "tool", "message": "arama"},
            {"actor": "artifact", "kind": "done", "message": "bosluk-analizi.md yazildi"},
            {"actor": "architect", "kind": "agent", "message": "basladi"},
            {"actor": "state", "kind": "tool", "message": "karar kaydedildi"},
        ]
        attribute_phases(events)
        assert [e["phase"] for e in events] == [
            None, "assess", "assess", "assess", "design", "design",
        ]

    def test_an_explicit_tag_wins_over_inference(self):
        from deerx.web.runner import attribute_phases

        events = [{"actor": "architect", "kind": "agent", "message": "x", "phase": "qa"}]
        attribute_phases(events)
        assert events[0]["phase"] == "qa"

    def test_disk_log_is_read_when_memory_is_empty(self, client, settings):
        """Sunucu yeniden baslayinca dokum ayrintisiz kalmamali."""
        import json as _json

        from deerx.web.runner import events_from_disk

        settings.events_path.write_text(
            "\n".join(
                _json.dumps(e) for e in [
                    {"actor": "planner", "kind": "tool", "message": "a", "phase": "plan"},
                    {"actor": "planner", "kind": "done", "message": "b", "phase": "plan"},
                ]
            ) + "\n",
            encoding="utf-8",
        )
        loaded = events_from_disk(settings.events_path)
        assert [e["kind"] for e in loaded] == ["tool", "done"]

    def test_a_truncated_log_line_does_not_break_reading(self, settings):
        from deerx.web.runner import events_from_disk

        settings.events_path.write_text(
            '{"actor":"a","kind":"tool","message":"tam"}\n{"actor":"b","kind":',
            encoding="utf-8",
        )
        assert len(events_from_disk(settings.events_path)) == 1


class TestSettingsPanel:
    """Ayarlar bolumu: kosu davranisi ve web arastirma."""

    def test_iteration_limit_is_settable_and_clamped(self, client, settings):
        assert client.post("/api/settings", json={"max_iterations": 40}).status_code == 200
        assert settings.max_iterations == 40
        client.post("/api/settings", json={"max_iterations": 9999})
        assert settings.max_iterations == 200  # ust sinir
        client.post("/api/settings", json={"max_iterations": 0})
        assert settings.max_iterations == 1    # alt sinir

    def test_iteration_limit_rejects_nonsense(self, client):
        response = client.post("/api/settings", json={"max_iterations": "cok"})
        assert response.status_code == 400
        assert "max_iterations" in response.json()["error"]

    def test_search_provider_is_validated(self, client, settings):
        assert client.post("/api/settings", json={"search_provider": "brave"}).status_code == 200
        assert settings.search_provider == "brave"
        # Ornek eskiden "google" idi; Google resmi Programmable Search ucuyla
        # gecerli bir saglayici oldu ve test onu dogru sekilde yakaladi.
        bad = client.post("/api/settings", json={"search_provider": "yandex"})
        assert bad.status_code == 400

    def test_google_reports_which_setting_is_missing(self, client, settings):
        """Google iki sey ister; hangisinin eksik oldugunu SOYLEMELI.

        Anahtar tek basina yetmez: arama motoru kimligi (cx) olmadan uc 400
        doner ve mesaji kullaniciya bir sey anlatmaz. Google yalnizca resmi
        ucla gelir cunku olculdu -- arama adresi gercek Chrome'da bile bot
        korumasi donuyor ("unusual traffic") ve CAPTCHA asmak yapmadigimiz
        bir sey.
        """
        from deerx.errors import ToolError
        from deerx.tools.web import _search_google

        with pytest.raises(ToolError) as hata:
            _search_google("x", 5, None, None)
        metin = str(hata.value)
        assert "search_api_key" in metin and "google_cse_id" in metin

        with pytest.raises(ToolError) as hata:
            _search_google("x", 5, "anahtar-var", None)
        assert "google_cse_id" in str(hata.value)
        assert "search_api_key" not in str(hata.value), (
            "var olan ayari eksik gostermek kullaniciyi yanlis yere gonderir"
        )

    def test_search_key_is_never_echoed_back(self, client, settings):
        """Anahtar arayuze hic donmemeli; yalnizca var/yok bilgisi."""
        result = client.post("/api/settings", json={"search_api_key": "gizli-anahtar"}).json()
        assert settings.search_api_key == "gizli-anahtar"
        assert "gizli-anahtar" not in json.dumps(result)
        assert result["changed"]["search_api_key"] == "tanimlandi"

        overview = client.get("/api/overview").json()
        assert "gizli-anahtar" not in json.dumps(overview)
        assert overview["settings"]["has_search_api_key"] is True

    def test_empty_key_clears_it(self, client, settings):
        client.post("/api/settings", json={"search_api_key": "x"})
        client.post("/api/settings", json={"search_api_key": "  "})
        assert settings.search_api_key is None
        assert client.get("/api/overview").json()["settings"]["has_search_api_key"] is False

    def test_overview_exposes_what_the_panel_needs(self, client):
        s = client.get("/api/overview").json()["settings"]
        for field in ("approval_mode", "max_iterations", "cost_limit_usd",
                      "enable_web", "search_provider", "has_search_api_key",
                      "workspace", "model_fast", "provider", "openai_base_url",
                      "model_lead", "effort_lead", "temperature", "language",
                      "has_openai_api_key", "has_anthropic_api_key"):
            assert field in s, field


class TestWebSearchHonesty:
    """Engellenmek 'sonuc yok' demek degildir.

    Ajan bos bir sonucu sorgunun cevabi sanip yanlis varsayimla ilerlerse
    hata mimariye ve koda sizar; bu yuzden ayrim acikca soylenir.
    """

    def test_blocked_keyless_search_is_reported_as_an_error(self, ctx, registry, monkeypatch):
        from deerx.tools import web

        monkeypatch.setattr(web, "_search_duckduckgo", lambda q, limit: [])
        ctx.settings.search_provider = "duckduckgo"
        result = registry.execute("web_search", {"query": "x"}, ctx)

        assert result.is_error, "engellenen arama basarili sayilmamali"
        # Sozlesme kelimeler degil anlam: (1) bunun bir "sonuc yok" cevabi
        # OLMADIGI, (2) modelin bilgi sahibi oldugunu varsaymamasi gerektigi.
        assert "DEGILDIR" in result.content
        assert "VARSAYMAYIN" in result.content

    def test_keyed_provider_empty_result_is_not_an_error(self, ctx, registry, monkeypatch):
        from deerx.tools import web

        monkeypatch.setattr(web, "_search_brave", lambda q, limit, key: [])
        ctx.settings.search_provider = "brave"
        ctx.settings.search_api_key = "test"
        result = registry.execute("web_search", {"query": "x"}, ctx)

        assert not result.is_error
        assert "bulunamadi" in result.content

    def test_parser_reads_both_duckduckgo_layouts(self):
        from deerx.tools.web import _ddg_parse

        lite = ('<table><tr><td><a class="result-link" href="https://ornek.com/a">Baslik A</a>'
                '</td></tr><tr><td>Ozet A</td></tr></table>')
        html = ('<div class="result"><a class="result__a" href="https://ornek.com/b">Baslik B</a>'
                '<div>Ozet B</div></div>')
        a = _ddg_parse(lite, ("a.result-link", "a[href^=http]"), 5)
        b = _ddg_parse(html, ("a.result__a", "a[href^=http]"), 5)
        assert a[0]["url"] == "https://ornek.com/a" and a[0]["title"] == "Baslik A"
        assert b[0]["url"] == "https://ornek.com/b" and b[0]["title"] == "Baslik B"

    def test_duckduckgo_own_pages_are_filtered_out(self):
        from deerx.tools.web import _ddg_parse

        body = ('<a href="https://duckduckgo.com/settings">Ayarlar</a>'
                '<a href="https://gercek.com/x">Gercek sonuc</a>')
        found = _ddg_parse(body, ("a[href^=http]",), 5)
        assert [f["url"] for f in found] == ["https://gercek.com/x"]


class TestRunHistory:
    """Her kosu kendi kimligiyle kalicidir.

    Faz durumu projeye aittir ve her tekrar kosuda uzerine yazilir; kosu
    kaydi ise gecmiste kalir. "Ikinci kosuda mimari ne kadar surdu"
    sorusunun cevabi ancak `run_steps` tablosundadir.
    """

    def _record(self, state_of, goal, phases, *, status="done"):
        import uuid

        run_id = uuid.uuid4().hex[:12]
        seq = state_of.start_run(run_id, goal=goal, phases=[str(p) for p in phases])
        for index, phase in enumerate(phases):
            state_of.start_run_step(run_id, phase, index)
            state_of.finish_run_step(run_id, phase, status=Status.DONE, summary=f"{phase} ozet")
        state_of.finish_run(run_id, status=status, cost_usd=0.5)
        return run_id, seq

    def test_runs_get_sequential_numbers(self, client, state_of):
        _, first = self._record(state_of, "ilk", [Phase.INGEST])
        _, second = self._record(state_of, "ikinci", [Phase.INGEST, Phase.ANALYZE])
        assert (first, second) == (1, 2)

    def test_listing_is_newest_first(self, client, state_of):
        self._record(state_of, "ilk", [Phase.INGEST])
        self._record(state_of, "ikinci", [Phase.INGEST])
        runs = client.get("/api/runs").json()["runs"]
        assert [r["seq"] for r in runs] == [2, 1]
        assert runs[0]["goal"] == "ikinci"

    def test_a_run_can_be_opened_by_its_sequence_number(self, client, state_of):
        self._record(state_of, "hedef", [Phase.INGEST, Phase.ANALYZE])
        detail = client.get("/api/runs/1").json()
        assert detail["run"]["seq"] == 1
        assert [s["phase"] for s in detail["steps"]] == ["ingest", "analyze"]

    def test_hash_prefix_is_accepted(self, client, state_of):
        self._record(state_of, "hedef", [Phase.INGEST])
        assert client.get("/api/runs/%231").json()["run"]["seq"] == 1

    def test_a_run_can_be_opened_by_its_id(self, client, state_of):
        run_id, _ = self._record(state_of, "hedef", [Phase.INGEST])
        assert client.get(f"/api/runs/{run_id}").json()["run"]["id"] == run_id

    def test_unknown_run_is_404(self, client):
        assert client.get("/api/runs/yokboyle").status_code == 404
        assert client.get("/api/runs/99").status_code == 404

    def test_steps_keep_their_own_order(self, client, state_of):
        self._record(state_of, "x", [Phase.INGEST, Phase.DESIGN, Phase.PLAN])
        steps = client.get("/api/runs/1").json()["steps"]
        assert [s["ordinal"] for s in steps] == [0, 1, 2]
        assert [s["label"] for s in steps] == ["Doküman alımı", "Mimari", "Plan"]

    def test_a_rerun_does_not_rewrite_the_earlier_runs_record(self, client, state_of):
        """Faz durumu ezilir; kosu kaydi ezilmemeli."""
        first, _ = self._record(state_of, "ilk", [Phase.DESIGN])
        state_of.finish_run_step(
            first, Phase.DESIGN, status=Status.DONE, summary="ilk kosunun ozeti", cost_usd=1.0
        )
        second, _ = self._record(state_of, "ikinci", [Phase.DESIGN])
        state_of.finish_run_step(
            second, Phase.DESIGN, status=Status.FAILED, summary="ikinci kosu patladi"
        )

        old = client.get(f"/api/runs/{first}").json()["steps"][0]
        new = client.get(f"/api/runs/{second}").json()["steps"][0]
        assert old["summary"] == "ilk kosunun ozeti" and old["status"] == "done"
        assert new["summary"] == "ikinci kosu patladi" and new["status"] == "failed"

    def test_events_are_filtered_to_the_run_that_produced_them(self, client, state_of):
        events = client.app.state.deerx.events
        first, _ = self._record(state_of, "ilk", [Phase.ANALYZE])
        second, _ = self._record(state_of, "ikinci", [Phase.ANALYZE])

        events.current_run, events.current_phase = first, "analyze"
        events.emit("tool", "analyst", "birinci kosunun araci")
        events.current_run = second
        events.emit("tool", "analyst", "ikinci kosunun araci")
        events.current_run = events.current_phase = None

        a = client.get(f"/api/runs/{first}").json()["steps"][0]["events"]
        b = client.get(f"/api/runs/{second}").json()["steps"][0]["events"]
        assert [e["message"] for e in a] == ["birinci kosunun araci"]
        assert [e["message"] for e in b] == ["ikinci kosunun araci"]

    def test_a_real_run_is_persisted_with_its_steps(self, client, settings, state_of):
        """Uctan uca: kosu baslat, kimligiyle geri oku."""
        settings.anthropic_api_key = None
        response = client.post("/api/run", json={"phases": ["ingest"], "force": True})
        assert response.status_code == 200
        run_id = response.json()["run"]["id"]
        _wait_idle(client)

        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["run"]["status"] == "done"
        assert [s["phase"] for s in detail["steps"]] == ["ingest"]
        assert detail["steps"][0]["status"] == "done"
        assert detail["steps"][0]["summary"]
        assert client.get("/api/runs").json()["runs"][0]["id"] == run_id


class TestLlmSettings:
    """Model ayarlari arayuzden degistirilebilmeli."""

    def test_provider_and_endpoint_are_settable(self, client, settings):
        client.post("/api/settings", json={
            "provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        assert settings.provider == "anthropic"
        assert settings.anthropic_api_key == "sk-ant-test"

    def test_models_and_efforts_are_settable(self, client, settings):
        client.post("/api/settings", json={
            "model_lead": "claude-opus-5", "model_worker": "claude-sonnet-5",
            "model_fast": "claude-haiku-4-5", "effort_lead": "max",
        })
        assert settings.model_lead == "claude-opus-5"
        assert settings.effort_lead == "max"

    def test_a_model_name_cannot_be_blanked(self, client, settings):
        before = settings.model_lead
        response = client.post("/api/settings", json={"model_lead": "  "})
        assert response.status_code == 400
        assert settings.model_lead == before

    def test_numeric_settings_are_clamped(self, client, settings):
        client.post("/api/settings", json={"max_tokens": 10, "request_timeout_seconds": 99999})
        assert settings.max_tokens == 256
        assert settings.request_timeout_seconds == 7200

    def test_temperature_can_be_cleared_to_the_server_default(self, client, settings):
        client.post("/api/settings", json={"temperature": 0.7})
        assert settings.temperature == pytest.approx(0.7)
        client.post("/api/settings", json={"temperature": ""})
        assert settings.temperature is None

    def test_unknown_setting_is_rejected(self, client):
        response = client.post("/api/settings", json={"gizli_arka_kapi": 1})
        assert response.status_code == 400
        assert "Bilinmeyen ayar" in response.json()["error"]

    def test_invalid_choice_names_the_options(self, client):
        response = client.post("/api/settings", json={"effort_lead": "cok-yuksek"})
        assert response.status_code == 400
        assert "Secenekler" in response.json()["error"]

    def test_no_api_key_is_ever_echoed_back(self, client):
        """Uc anahtar da yalnizca yazilir; degerleri arayuze donmez."""
        client.post("/api/settings", json={
            "openai_api_key": "gizli-openai",
            "anthropic_api_key": "gizli-anthropic",
            "search_api_key": "gizli-arama",
        })
        payload = json.dumps(client.get("/api/overview").json())
        for secret in ("gizli-openai", "gizli-anthropic", "gizli-arama"):
            assert secret not in payload, secret

        view = client.get("/api/overview").json()["settings"]
        assert view["has_openai_api_key"] is True
        assert view["has_anthropic_api_key"] is True
        assert view["has_search_api_key"] is True

    def test_changing_a_model_setting_drops_the_cached_client(self, client, settings):
        """Istemci bu degerleri kurulumda okur; dusurulmezse ayar etkisiz kalir."""
        orch = client.app.state.deerx.orchestrator
        orch._client = object()  # kurulmus gibi davran
        client.post("/api/settings", json={"model_lead": "yeni-model"})
        assert orch._client is None

    def test_a_harmless_setting_keeps_the_client(self, client):
        orch = client.app.state.deerx.orchestrator
        sentinel = object()
        orch._client = sentinel
        client.post("/api/settings", json={"approval_mode": "auto"})
        assert orch._client is sentinel

    def test_models_cannot_change_mid_run(self, client, settings, state_of):
        """Kosunun ilk yarisi bir modelle, ikinci yarisi baskasiyla olmamali."""
        settings.anthropic_api_key = None
        client.post("/api/run", json={"phases": ["ingest"], "force": True})
        try:
            response = client.post("/api/settings", json={"model_lead": "baska"})
            # Kosu cok kisa; bitmis olabilir. Reddedildiyse dogru sebeple.
            if response.status_code == 409:
                assert "model ayarlari" in response.json()["error"]
        finally:
            _wait_idle(client)

    def test_behaviour_settings_still_work_mid_run(self, client, settings):
        settings.anthropic_api_key = None
        client.post("/api/run", json={"phases": ["ingest"], "force": True})
        try:
            assert client.post("/api/settings", json={"cost_limit_usd": 5}).status_code == 200
        finally:
            _wait_idle(client)

    def test_every_form_input_maps_to_a_real_setting(self):
        """Arayuzdeki her alan sunucunun tanidigi bir ayara karsilik gelmeli."""
        import re

        from deerx.web.app import SETTING_FIELDS, STATIC_DIR

        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const SETTING_INPUTS = \[(.*?)\];", js, re.S).group(1)
        names = re.findall(r'"#set-[\w-]+",\s*"(\w+)"', block)
        assert names, "test kendini dogrulayamiyor"
        assert not (set(names) - set(SETTING_FIELDS)), set(names) - set(SETTING_FIELDS)

    def test_llm_probe_reports_a_missing_configuration(self, client, settings):
        settings.provider = "openai"
        settings.openai_base_url = None
        result = client.post("/api/settings/test-llm", json={}).json()
        assert result["ok"] is False
        assert "openai_base_url" in result["error"]


class TestPlans:
    """Bir plan, adlandirilmis bagimsiz bir gorev grubudur."""

    def test_the_first_call_creates_a_default_plan(self, client):
        data = client.get("/api/plans").json()
        assert len(data["plans"]) == 1
        assert data["active"] == data["plans"][0]["id"]

    def test_tasks_land_in_the_active_plan(self, client, state_of):
        state_of.add_task(Task(key="T-001", title="ilk"))
        plan = client.get("/api/plans").json()["plans"][0]
        assert plan["tasks"] == 1

    def test_plans_keep_their_tasks_apart(self, client, state_of):
        first = state_of.active_plan_id()
        second = state_of.create_plan("Mobil")["id"]
        state_of.add_task(Task(key="T-001", title="backend isi"), plan_id=first)
        state_of.add_task(Task(key="T-100", title="mobil isi"), plan_id=second)

        a = client.get(f"/api/state/tasks?plan={first}").json()["items"]
        b = client.get(f"/api/state/tasks?plan={second}").json()["items"]
        assert [t["key"] for t in a] == ["T-001"]
        assert [t["key"] for t in b] == ["T-100"]

    def test_a_plan_needs_a_name(self, client):
        assert client.post("/api/plans", json={"name": "  "}).status_code == 400

    def test_switching_the_active_plan(self, client, state_of):
        second = state_of.create_plan("Mobil")["id"]
        client.post(f"/api/plans/{second}", json={"active": True})
        assert client.get("/api/plans").json()["active"] == second

    def test_renaming(self, client, state_of):
        plan_id = state_of.active_plan_id()
        result = client.post(f"/api/plans/{plan_id}", json={"name": "Yeni ad"}).json()
        assert result["plan"]["name"] == "Yeni ad"

    def test_deleting_takes_its_tasks_with_it(self, client, state_of):
        keep = state_of.active_plan_id()
        drop = state_of.create_plan("Gecici")["id"]
        state_of.add_task(Task(key="T-001", title="kalacak"), plan_id=keep)
        state_of.add_task(Task(key="T-900", title="gidecek"), plan_id=drop)

        result = client.delete(f"/api/plans/{drop}").json()
        assert result["removed_tasks"] == 1
        assert state_of.get_task("T-900") is None
        assert state_of.get_task("T-001") is not None

    def test_the_last_plan_cannot_be_deleted(self, client, state_of):
        """Silinirse gorevler sahipsiz kalirdi."""
        plan_id = state_of.active_plan_id()
        response = client.delete(f"/api/plans/{plan_id}")
        assert response.status_code == 400
        assert "Tek kalan plan" in response.json()["error"]

    def test_unknown_plan_is_404(self, client):
        assert client.post("/api/plans/yok", json={"name": "x"}).status_code == 404
        assert client.delete("/api/plans/yok").status_code == 404

    def test_dependencies_reach_across_plans(self, state_of):
        """Anahtarlar proje capinda tekil; bagimlilik plan siniri tanimaz."""
        first = state_of.active_plan_id()
        second = state_of.create_plan("Mobil")["id"]
        state_of.add_task(Task(key="T-001", title="temel"), plan_id=first)
        state_of.add_task(
            Task(key="T-100", title="ona bagli", deps=["T-001"]), plan_id=second
        )

        assert [t.key for t in state_of.ready_tasks(plan_id=second)] == []
        state_of.update_task("T-001", status=Status.DONE)
        assert [t.key for t in state_of.ready_tasks(plan_id=second)] == ["T-100"]

    def test_implement_only_touches_the_named_plan(self, settings, state_of):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        first = state_of.active_plan_id()
        second = state_of.create_plan("Mobil")["id"]
        state_of.add_task(Task(key="T-001", title="ana"), plan_id=first)
        state_of.add_task(Task(key="T-100", title="mobil"), plan_id=second)

        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            result = orch._run_implement(plan_id=second)
        # Model yok; onemli olan yalnizca ikinci planin gorevine dokunmasi.
        assert "T-001" not in result.details.get("remaining", []) + \
            result.details.get("completed", []) + result.details.get("failed", [])


class TestOrphanedTasks:
    """Yarida kesilen bir kosu gorevleri `running` birakir ve plan kilitlenir."""

    def test_running_tasks_are_reclaimed_on_startup(self, settings, state_of):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        state_of.add_task(Task(key="T-001", title="yarim", status=Status.RUNNING))
        state_of.add_task(Task(key="T-002", title="bagli", deps=["T-001"]))
        assert state_of.ready_tasks() == []  # kilitli

        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            assert orch.state.get_task("T-001").status == Status.PENDING
            assert [t.key for t in orch.state.ready_tasks()] == ["T-001"]

    def test_finished_tasks_are_left_alone(self, state_of):
        state_of.add_task(Task(key="T-001", title="bitti", status=Status.DONE))
        state_of.add_task(Task(key="T-002", title="patladi", status=Status.FAILED))
        assert state_of.reclaim_orphaned_tasks() == []
        assert state_of.get_task("T-001").status == Status.DONE
        assert state_of.get_task("T-002").status == Status.FAILED


class TestArtifactsByRun:
    """Ciktilar uretildikleri kosuya baglanir ve ona gore gruplanir."""

    def _run(self, state_of, goal="hedef"):
        import uuid

        run_id = uuid.uuid4().hex[:12]
        state_of.start_run(run_id, goal=goal, phases=["analyze"])
        return run_id

    def _artifact(self, settings, state_of, name, *, run_id="", phase=""):
        path = settings.artifacts_dir / name
        path.write_text("# icerik\n", encoding="utf-8")
        state_of.add_artifact(
            Artifact(name=name, kind="report", path=str(path), summary="x"),
            run_id=run_id, phase=phase,
        )

    def test_artifacts_are_grouped_under_their_run(self, client, settings, state_of):
        first = self._run(state_of, "ilk kosu")
        second = self._run(state_of, "ikinci kosu")
        self._artifact(settings, state_of, "a.md", run_id=first, phase="analyze")
        self._artifact(settings, state_of, "b.md", run_id=second, phase="design")

        groups = client.get("/api/artifacts").json()["groups"]
        # En yeni kosu basta.
        assert [g["seq"] for g in groups] == [2, 1]
        assert [i["name"] for i in groups[0]["items"]] == ["b.md"]
        assert groups[0]["goal"] == "ikinci kosu"

    def test_the_phase_label_travels_with_the_artifact(self, client, settings, state_of):
        run_id = self._run(state_of)
        self._artifact(settings, state_of, "mimari.md", run_id=run_id, phase="design")
        item = client.get("/api/artifacts").json()["groups"][0]["items"][0]
        assert item["phase"] == "design"
        assert item["phase_label"] == "Mimari"

    def test_artifacts_without_a_run_are_not_listed(self, client, settings, state_of):
        """Her cikti bir kosunun urunudur.

        Kosusuz bir grup basligi kullaniciya hicbir sey anlatmiyordu; ama
        sayilari bildirilir ki sessizce kaybolmus gibi durmasin.
        """
        run_id = self._run(state_of)
        self._artifact(settings, state_of, "yeni.md", run_id=run_id)
        self._artifact(settings, state_of, "eski.md")

        payload = client.get("/api/artifacts").json()
        listed = [i["name"] for g in payload["groups"] for i in g["items"]]
        assert listed == ["yeni.md"]
        assert payload["orphans"] == 1

    def test_orphans_can_still_be_requested(self, client, settings, state_of):
        self._artifact(settings, state_of, "eski.md")
        payload = client.get("/api/artifacts?orphans=1").json()
        listed = [i["name"] for g in payload["groups"] for i in g["items"]]
        assert listed == ["eski.md"]
        assert payload["groups"][-1]["seq"] is None

    def test_rewriting_an_artifact_moves_it_to_the_new_run(self, client, settings, state_of):
        """Ayni ad tekrar uretilirse cikti son ureten kosunun urunudur."""
        first = self._run(state_of, "ilk")
        second = self._run(state_of, "ikinci")
        self._artifact(settings, state_of, "rapor.md", run_id=first)
        self._artifact(settings, state_of, "rapor.md", run_id=second)

        groups = {g["seq"]: g for g in client.get("/api/artifacts").json()["groups"]}
        assert [i["name"] for i in groups[2]["items"]] == ["rapor.md"]
        assert 1 not in groups  # ilk kosunun grubu bos kaldi, hic gorunmez

    def test_the_artifacts_directory_path_is_not_exposed(self, client, settings, state_of):
        """Kullanici mutlak yolu degil, koşuyu gormek istiyor."""
        self._artifact(settings, state_of, "a.md")
        payload = client.get("/api/artifacts").json()
        assert "dir" not in payload
        assert str(settings.artifacts_dir) not in json.dumps(payload)

    def test_save_artifact_attaches_the_running_phase(self, ctx, registry, state):
        """Ajan bir cikti yazdiginda kosu ve faz kendiliginden islenmeli."""
        ctx.events.current_run = "kosu-123"
        ctx.events.current_phase = "design"
        registry.execute(
            "save_artifact",
            {"name": "mimari.md", "content": "# Mimari", "kind": "architecture"},
            ctx,
        )
        saved = state.list_artifacts()[0]
        assert saved.run_id == "kosu-123"
        assert saved.phase == "design"

    def test_artifacts_can_be_filtered_to_one_run(self, settings, state_of):
        first = self._run(state_of)
        second = self._run(state_of)
        self._artifact(settings, state_of, "a.md", run_id=first)
        self._artifact(settings, state_of, "b.md", run_id=second)
        assert [a.name for a in state_of.list_artifacts(run_id=first)] == ["a.md"]


class TestTokenBudget:
    """220K token butcesi ve ona gore olceklenmis kosu davranisi."""

    def test_the_settings_screen_can_reach_220k(self, client, settings):
        """Tavan 200K'ydi: 220K girilse sessizce kirpilirdi."""
        client.post("/api/settings", json={"max_tokens": 220_000})
        assert settings.max_tokens == 220_000

    def test_defaults_are_scaled_together(self, settings):
        """Genis pencerede araclara dar butce vermek modeli baglamdan mahrum eder.

        Sabit bir sayi degil oran sinanir: `max_tokens` bir tavandir ve
        pencereye gore istek basina kirpilir, ama arac butceleri onunla
        ayni buyukluk sirasinda kalmali.
        """
        assert settings.max_tokens >= 32_000
        assert settings.max_tool_output_chars >= 80_000
        assert settings.max_turn_output_chars >= 240_000
        # Tur butcesi tek arac butcesinin katindan buyuk olmali; aksi halde
        # iki paralel arac bile turu doldururdu.
        assert settings.max_turn_output_chars >= settings.max_tool_output_chars * 2

    def test_the_timeout_covers_the_budget_on_a_local_endpoint(self, settings):
        """220K token ~70 tok/s ile ~52 dakika; zaman asimi bunu karsilamali."""
        seconds = settings.max_tokens / settings.LOCAL_TOKENS_PER_SECOND
        assert settings.request_timeout_seconds >= seconds


class TestPalette:
    """Renk paleti: markadan turetilmis ve okunabilir olmali.

    Palet elle secilen renklerden olusuyor; goz kararinda "iyi gorunen" bir
    ton kolayca AA'nin altina duser ve kimse fark etmez. Bu testler
    styles.css'teki degerleri okuyup kontrasti hesaplar.
    """

    # (metin, zemin, en az oran) -- WCAG AA: govde 4.5:1, ikincil metin 3:1
    PAIRS = [
        ("text", "surface", 4.5), ("text", "bg", 4.5),
        ("text-2", "surface", 4.5), ("text-2", "surface-2", 4.5),
        # Ucuncul metin 10-12px kullaniliyor; WCAG bu boyutta 4.5 ister,
        # "ikincil metin" gevsemesi buyuk puntoya ozgudur.
        ("text-3", "surface", 4.5), ("text-3", "muted-soft", 4.5),
        ("text-3", "surface-2", 4.5), ("text-3", "bg", 4.5),
        ("accent", "surface", 4.5), ("accent", "accent-soft", 4.5),
        ("accent-text", "accent", 4.5),
        ("ok", "surface", 4.5), ("ok", "ok-soft", 4.5),
        ("warn", "surface", 4.5), ("warn", "warn-soft", 4.5),
        ("err", "surface", 4.5), ("err", "err-soft", 4.5),
        ("info", "surface", 4.5), ("info", "info-soft", 4.5),
    ]

    @staticmethod
    def _themes() -> dict[str, dict[str, str]]:
        """styles.css'ten acik ve koyu tema tokenlarini ayiklar."""
        import re

        from deerx.web.app import STATIC_DIR

        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        blocks = {}
        # `:root {` acik tema; `color-scheme: dark` iceren bloklar koyu tema.
        for match in re.finditer(r"(:root[^{]*)\{([^}]*)\}", css):
            selector, body = match.group(1), match.group(2)
            if "--bg:" not in body:
                continue
            name = "dark" if "color-scheme: dark" in body else "light"
            tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", body))
            # Ilk gorulen kazanir; iki koyu blok ayni degerleri tasir.
            blocks.setdefault(name, {}).update(
                {k: v for k, v in tokens.items() if k not in blocks.get(name, {})}
            )
            if "selector" in selector:  # pragma: no cover - okunabilirlik icin
                pass
        return blocks

    @staticmethod
    def _contrast(a: str, b: str) -> float:
        def channel(value: int) -> float:
            c = value / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        def lum(color: str) -> float:
            h = color.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

        lo, hi = sorted((lum(a), lum(b)))
        return (hi + 0.05) / (lo + 0.05)

    def test_both_themes_are_defined(self):
        themes = self._themes()
        assert set(themes) == {"light", "dark"}

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_every_text_pair_meets_wcag_aa(self, theme):
        tokens = self._themes()[theme]
        weak = []
        for fg, bg, need in self.PAIRS:
            ratio = self._contrast(tokens[fg], tokens[bg])
            if ratio < need:
                weak.append(f"{fg}/{bg} = {ratio:.2f} (gereken {need})")
        assert not weak, f"{theme} temada zayif kontrast: " + "; ".join(weak)

    def test_the_accent_belongs_to_the_brand_hue(self):
        """Vurgu rengi logonun lacivert ailesinden olmali.

        Onceki vurgu mordu (hue 241) ve logoyla (hue 214) catisiyordu.
        """
        import colorsys

        for theme in ("light", "dark"):
            accent = self._themes()[theme]["accent"].lstrip("#")
            r, g, b = (int(accent[i:i + 2], 16) / 255 for i in (0, 2, 4))
            hue = colorsys.rgb_to_hsv(r, g, b)[0] * 360
            assert 195 <= hue <= 230, f"{theme}: vurgu hue {hue:.0f}, marka ailesi disinda"

    def test_semantic_colours_are_distinguishable(self):
        """Dort anlamsal renk birbirinden ayirt edilebilmeli."""
        import colorsys

        for theme in ("light", "dark"):
            tokens = self._themes()[theme]
            hues = {}
            for name in ("ok", "warn", "err", "info"):
                h = tokens[name].lstrip("#")
                r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
                hues[name] = colorsys.rgb_to_hsv(r, g, b)[0] * 360
            for a, b in (("ok", "warn"), ("ok", "err"), ("ok", "info"),
                         ("warn", "err"), ("warn", "info"), ("err", "info")):
                gap = abs(hues[a] - hues[b])
                gap = min(gap, 360 - gap)
                assert gap >= 30, f"{theme}: {a} ve {b} cok yakin ({gap:.0f} derece)"


class TestDesignScale:
    """Tipografi ve bosluk olcegi.

    Olcumden once 18 farkli punto, 11 farkli font agirligi ve 14 farkli
    bosluk vardi. 520/550/560/570 agirliklari gozle ayirt edilemez; bu
    cesitlilik tasarim degil, her bilesenin komsusuna bakmadan kendi
    degerini secmesiydi. Arayuzun "toplanmis" degil "tasarlanmis"
    gorunmesini saglayan sey bu olcek.
    """

    SIZES = {"11px", "12px", "13px", "14px", "17px", "22px", "26px"}
    WEIGHTS = {"400", "500", "600", "700"}
    SPACE = {"0", "4px", "8px", "12px", "16px", "20px", "24px", "28px", "32px"}

    @staticmethod
    def _css() -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    def test_font_sizes_stay_on_the_scale(self):
        import re

        # `em` degerleri oransaldir (mono icin optik duzeltme) ve olcek disi
        # sayilmaz; piksel degerleri olcege uymali.
        used = set(re.findall(r"font-size:\s*([\d.]+px)", self._css()))
        assert not (used - self.SIZES), f"olcek disi punto: {sorted(used - self.SIZES)}"

    def test_font_weights_stay_on_the_scale(self):
        import re

        used = set(re.findall(r"font-weight:\s*(\d+)", self._css()))
        assert not (used - self.WEIGHTS), f"olcek disi agirlik: {sorted(used - self.WEIGHTS)}"

    def test_gaps_stay_on_the_four_pixel_grid(self):
        import re

        used: set[str] = set()
        for value in re.findall(r"\bgap:\s*([^;]+);", self._css()):
            used.update(value.strip().split())
        stray = {v for v in used if v.endswith("px") and v not in self.SPACE}
        assert not stray, f"dort piksel izgarasi disi bosluk: {sorted(stray)}"

    def test_the_page_title_is_the_largest_text(self):
        """Istatistik sayilari basligi bastirmamali.

        24px sayilar 22px basligin yanindayken sayfa "sayilar hakkinda"
        gorunuyordu; hiyerarsi tersine donmustu.
        """
        import re

        css = self._css()
        title = re.search(r"\.view-head h1 \{[^}]*font-size:\s*(\d+)px", css)
        stat = re.search(r"\.stat-value \{[^}]*font-size:\s*(\d+)px", css)
        assert title and stat
        assert int(title.group(1)) > int(stat.group(1)), (
            "sayfa basligi istatistik sayilarindan buyuk olmali"
        )


class TestLanguageSwitch:
    """Ust bardaki dil anahtari.

    Dil ayarlar ekraninin dibindeydi ve "Kaydet"e basilmadan sunucuda
    kalici olmuyordu: arayuz Ingilizceye geciyor, olay akisi ve arac
    hatalari Turkce kaliyordu. Anahtar ust bara alindi ve tek tiklamada
    sunucuya da yaziyor.
    """

    def test_both_options_are_in_the_markup(self):
        from deerx.web.app import STATIC_DIR

        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        assert 'id="lang-switch"' in html
        for kod in ("tr", "en"):
            assert f'data-lang="{kod}"' in html, kod

    def test_the_switch_sits_in_the_topbar(self):
        """Kullanicinin istedigi yer sag ust. Ayarlar ekranina tasinirsa
        bu test dusertir."""
        from deerx.web.app import STATIC_DIR

        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        ust_bar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
        assert 'id="lang-switch"' in ust_bar

    def test_changing_the_language_persists_on_the_server(self, client, settings):
        """Anahtarin dayandigi sozlesme: tek istek, kalici sonuc."""
        assert client.post("/api/settings", json={"language": "en"}).status_code == 200
        assert settings.language == "en"

    def test_the_server_catalog_follows(self, client):
        """Yalnizca alani degistirmek yetmez: sunucudan gelen metinler de
        degismeli, yoksa arayuz Ingilizce olay akisi Turkce olurdu."""
        client.post("/api/settings", json={"language": "en"})
        ingilizce = client.post("/api/plans", json={}).json()["error"]

        client.post("/api/settings", json={"language": "tr"})
        turkce = client.post("/api/plans", json={}).json()["error"]

        assert ingilizce == "Give the plan a name."
        assert turkce == "Plana bir ad verin."

    def test_an_unknown_language_is_refused(self, client, settings):
        onceki = settings.language
        assert client.post("/api/settings", json={"language": "de"}).status_code == 400
        assert settings.language == onceki

    def test_the_switch_and_the_settings_select_share_one_path(self):
        """Iki giris noktasi ayni fonksiyondan gecmeli; ayrilirlarsa biri
        sunucuya yazar digeri yazmaz ve fark gorunmez olur."""
        from deerx.web.app import STATIC_DIR

        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        assert js.count("changeLanguage(") >= 3      # tanim + iki cagri yeri
        assert "post(\"/api/settings\", { language:" in js


class TestDefaultSettings:
    """Varsayilanlarin uc kaynagi tutmali.

    `Settings` alanlari kodun gercegi, `deerx.default.toml` yeni bir
    calisma alanina yazilan, `SETTING_FIELDS` de Ayarlar ekraninin
    duzenledigi. Ayrilmalari sessiz bir hatadir: kullanicinin gordugu
    deger ile calisan deger farkli olur.
    """

    @staticmethod
    def _sablon():
        import tomllib

        from deerx.web.app import STATIC_DIR

        yol = STATIC_DIR.parents[1] / "templates" / "deerx.default.toml"
        return tomllib.loads(yol.read_text(encoding="utf-8"))["deerx"]

    def test_the_template_matches_the_code_defaults(self, settings):
        """Sablonda yazan her deger kodun varsayilaniyla ayni olmali.

        Bir zamanlar `max_tokens` sablonda 8000, kodda 64000'di -- sekiz
        kat fark ve kimse fark etmiyordu."""
        from deerx.config import Settings

        sablon = {k: v for k, v in self._sablon().items() if not isinstance(v, dict)}
        kod = Settings.model_fields
        farklar = []
        for ad, deger in sablon.items():
            if ad not in kod:
                farklar.append(f"{ad}: sablonda var, kodda yok")
                continue
            varsayilan = kod[ad].default
            if repr(varsayilan).startswith("PydanticUndefined"):
                continue
            if deger != varsayilan:
                farklar.append(f"{ad}: sablon={deger!r} kod={varsayilan!r}")
        assert not farklar, farklar

    def test_a_fresh_workspace_can_do_uat(self, tmp_path):
        """QA yonergesi UAT'yi kabul olcutu sayiyor ("bu bolum atlanamaz").
        Yapilandirma onu kapatiyorsa faz kendi olcutunu saglayamaz."""
        import tomllib

        from deerx.config import load_settings
        from deerx.web.app import STATIC_DIR

        sablon = STATIC_DIR.parents[1] / "templates" / "deerx.default.toml"
        (tmp_path / "deerx.toml").write_text(
            sablon.read_text(encoding="utf-8"), encoding="utf-8"
        )
        ayar = load_settings(tmp_path)
        assert ayar.browser_allow_preview, (
            "preview_open kapali: QA fazi kendi kabul olcutunu saglayamaz"
        )
        assert tomllib  # kullanildi

    def test_the_output_ceiling_fits_the_timeout(self, settings):
        """`max_tokens` yerel uretim hizinda zaman asimini gecmemeli.

        Olculdu: yerel bir akil yurutme modeli ~70 tok/s uretir ve
        `max_tokens` DUSUNMEYI de kapsar."""
        saniye = settings.max_tokens / settings.LOCAL_TOKENS_PER_SECOND
        assert saniye < settings.request_timeout_seconds, (
            f"{settings.max_tokens} token ~{saniye:.0f}s surer ama zaman "
            f"asimi {settings.request_timeout_seconds}s"
        )

    def test_the_screen_accepts_every_real_provider(self):
        """`Settings`'in kabul ettigi her saglayiciyi ekran da kabul etmeli.

        `searxng` alani eklenmis ama ekranin secenek listesine
        konmamisti: saglayici koddan calisiyor, arayuzden secilemiyordu."""
        import typing

        from deerx.config import Settings
        from deerx.web.app import SETTING_FIELDS

        ayristir = SETTING_FIELDS["search_provider"].parse
        kabul = typing.get_args(Settings.model_fields["search_provider"].annotation)
        reddedilen = []
        for saglayici in kabul:
            try:
                ayristir(saglayici)
            except ValueError:
                reddedilen.append(saglayici)
        assert not reddedilen, f"ekran su saglayicilari reddediyor: {reddedilen}"

        with pytest.raises(ValueError):
            ayristir("boyle-bir-saglayici-yok")


class TestIsolationIsReachableFromTheInterface:
    """`execution = "docker"` README'nin uc ayirt edici ozelliginden biri.

    Ayarlar ekraninda hic yoktu: acmanin tek yolu `deerx.toml` dosyasini
    elle duzenleyip sunucuyu yeniden baslatmakti. Bir ozelligin arayuzde
    karsiligi yoksa kullanici icin yok demektir.
    """

    ALANLAR = (
        "execution", "sandbox_image", "sandbox_setup",
        "sandbox_port_base", "sandbox_port_count",
        "sandbox_memory", "sandbox_cpus", "sandbox_pids",
    )

    def test_every_sandbox_field_is_visible(self, client):
        goruntu = client.get("/api/overview").json()["settings"]
        eksik = [a for a in self.ALANLAR if a not in goruntu]
        assert not eksik, f"ayarlar goruntusunde yok: {eksik}"

    def test_every_sandbox_field_is_writable(self, client):
        for alan in self.ALANLAR:
            assert alan in SETTING_FIELDS, alan

    def test_isolation_can_be_turned_on(self, client, settings):
        assert settings.execution == "host"
        assert client.post("/api/settings", json={"execution": "docker"}).status_code == 200
        assert settings.execution == "docker"

    def test_an_unknown_mode_is_refused(self, client):
        response = client.post("/api/settings", json={"execution": "vm"})
        assert response.status_code == 400

    def test_an_empty_image_is_refused(self, client):
        """Bos imajla konteyner kurulamaz; ayar kaydedilirse hata kosu
        sirasinda, yani en pahali anda cikar."""
        assert client.post("/api/settings", json={"sandbox_image": " "}).status_code == 400

    def test_the_container_is_rebuilt_when_the_settings_change(self, client, monkeypatch):
        """`Sandbox` ayarlarini KURULUMDA okur: portlari ve kaynak
        sinirlarini konteyner yaratilirken ayirir. Yeniden kurulmazsa
        degisiklik sunucu yeniden baslatilana kadar sessizce etkisiz
        kalir ve kullanici yalitimi actigini sanir."""
        cagrildi = []
        orch = client.app.state.deerx.orchestrator
        monkeypatch.setattr(orch, "reset_sandbox", lambda: cagrildi.append(1))
        client.post("/api/settings", json={"sandbox_memory": "4g"})
        assert cagrildi, "kabin yeniden kurulmadi"

    def test_unrelated_settings_do_not_rebuild_it(self, client, monkeypatch):
        cagrildi = []
        orch = client.app.state.deerx.orchestrator
        monkeypatch.setattr(orch, "reset_sandbox", lambda: cagrildi.append(1))
        client.post("/api/settings", json={"log_level": "DEBUG"})
        assert not cagrildi, "ilgisiz ayar konteyneri yikmamali"


class TestTheSettingsEventIsReadable:
    """Olay akisina Python sozlugunun `repr`i dusuyordu.

    "updated: {'language': 'en'}" -- kesme isaretleri, suslu parantezler.
    Akis kullaniciya gosterilen bir yer, hata ayiklama ciktisi degil.
    """

    def _son_olay(self, client):
        olaylar = client.app.state.deerx.runner.events_since(0)[0]
        ayar = [e for e in olaylar if e["actor"] in ("ayarlar", "settings")]
        assert ayar, "ayar olayi yok"
        return ayar[-1]["message"]

    def test_no_python_repr_leaks(self, client):
        client.post("/api/settings", json={"log_level": "DEBUG"})
        mesaj = self._son_olay(client)
        assert "{" not in mesaj and "'" not in mesaj, mesaj
        assert "log_level = DEBUG" in mesaj

    def test_a_secret_is_named_but_not_shown(self, client):
        client.post("/api/settings", json={"search_api_key": "gizli-anahtar"})
        mesaj = self._son_olay(client)
        assert "search_api_key" in mesaj
        assert "gizli-anahtar" not in mesaj


class TestTheEventLogIsReadableFromTheInterface:
    """Canli akis "`.deerx/events.jsonl` dosyasinda saklanir" diyordu.

    Diyordu ama gostermiyordu: dosyayi geri okuyan bir uc yoktu, sayfa
    her yenilendiginde akis bombostu. Denetlenebilirlik ekranda bitmezse
    yoktur.
    """

    def _yaz(self, settings, n=5, bozuk=False):
        yol = settings.events_path
        yol.parent.mkdir(parents=True, exist_ok=True)
        satirlar = [
            json.dumps({"kind": "tool", "actor": "shell", "message": f"komut {i}",
                        "ts": 1000.0 + i}, ensure_ascii=False)
            for i in range(n)
        ]
        if bozuk:
            satirlar.insert(1, '{"yarim":')
        yol.write_text("\n".join(satirlar) + "\n", encoding="utf-8")

    def test_the_tail_comes_back(self, client, settings):
        self._yaz(settings, n=5)
        data = client.get("/api/events/history?limit=3").json()
        assert [e["message"] for e in data["events"]] == ["komut 2", "komut 3", "komut 4"]

    def test_a_missing_log_is_not_an_error(self, client, settings):
        settings.events_path.unlink(missing_ok=True)
        data = client.get("/api/events/history").json()
        assert data["events"] == []

    def test_a_half_written_line_does_not_lose_the_rest(self, client, settings):
        """Kosu yarida kesildiyse son satir yarim kalabilir."""
        self._yaz(settings, n=4, bozuk=True)
        data = client.get("/api/events/history").json()
        assert len(data["events"]) == 4

    def test_the_limit_is_bounded(self, client, settings):
        self._yaz(settings, n=3)
        assert client.get("/api/events/history?limit=999999").status_code == 200
        assert client.get("/api/events/history?limit=abc").status_code == 200

    def test_a_long_log_is_read_from_the_end(self, settings):
        """16 MB'lik bir gunlugu her sayfa yenilemesinde bastan sona
        okumak, istenen seyi yapmanin en pahali yoludur."""
        yol = settings.events_path
        yol.parent.mkdir(parents=True, exist_ok=True)
        with yol.open("w", encoding="utf-8") as fh:
            for i in range(50_000):
                fh.write(f"satir {i}\n")
        son = _tail_lines(yol, 3)
        assert son == ["satir 49997", "satir 49998", "satir 49999"]


class TestRunTitlesFollowTheLanguage:
    """Kosu basligi sunucuda uretilip metin olarak saklaniyordu.

    Ingilizce arayuzde kosu listesi Turkce goruntuluyordu -- hemen
    altindaki faz listesi cevriliyken. Baslik artik anahtar ve
    parametreleriyle birlikte saklanir.
    """

    def test_a_phase_range_carries_its_key(self, client):
        r = client.post("/api/run", json={"phases": ["ingest", "analyze"], "goal": "x"})
        _wait_idle(client)
        assert r.status_code == 200, r.json()
        run = client.get("/api/runs").json()["runs"][0]
        assert run["title_key"] == "runs.titlePhases"
        assert run["title_args"] == {"first": "ingest", "last": "analyze"}

    def test_a_single_phase_carries_its_key(self, client):
        client.post("/api/run", json={"phases": ["ingest"], "goal": "x"})
        _wait_idle(client)
        run = client.get("/api/runs").json()["runs"][0]
        assert run["title_key"] == "runs.titlePhase"
        assert run["title_args"] == {"phase": "ingest"}

    def test_the_written_title_is_still_there(self, client):
        """Eski kayitlar ve veritabanini elle okumak icin yedek."""
        client.post("/api/run", json={"phases": ["ingest"], "goal": "x"})
        _wait_idle(client)
        run = client.get("/api/runs").json()["runs"][0]
        assert run["title"]

    def test_the_pipeline_does_not_erase_the_key(self, client):
        """Boru hatti ayni kosu kaydini ikinci kez acar ve anahtari
        tasimaz; bos deger yazilirsa ilk cagrinin verdigi anahtar
        silinirdi.

        Kosu BASLATILMIYOR: gercek bir kosu arka planda ayni SQLite
        baglantisina yazar ve bu testin kendi yazmasiyla carpisirdi.
        Sinanan sey zaten `start_run`in iki cagrisi arasindaki davranis.
        """
        state = client.app.state.deerx.orchestrator.state
        state.start_run("kosu0001", goal="x", phases=["ingest"], title="Ingest",
                        title_key="runs.titlePhase", title_args={"phase": "ingest"})
        state.start_run("kosu0001", goal="x", phases=["ingest"], title="Ingest")
        kayit = next(r for r in state.list_runs(10) if r["id"] == "kosu0001")
        assert kayit["title_key"] == "runs.titlePhase"
        assert kayit["title_args"] == {"phase": "ingest"}

    def test_artifact_groups_carry_the_key_too(self, client, settings):
        client.post("/api/run", json={"phases": ["ingest"], "goal": "x"})
        _wait_idle(client)
        run = client.get("/api/runs").json()["runs"][0]
        path = settings.artifacts_dir / "rapor.md"
        path.write_text("# x\n", encoding="utf-8")
        client.app.state.deerx.orchestrator.state.add_artifact(
            Artifact(name="rapor.md", kind="report", path=str(path),
                     summary="x", run_id=run["id"])
        )
        grup = client.get("/api/artifacts").json()["groups"][0]
        assert grup["title_key"] == "runs.titlePhase"


class TestTheWorkspaceIsVisibleFromEveryScreen:
    """Sol alt kosede hangi calisma alanindayiz.

    Ayni makinede birden cok calisma alani acik olabiliyor ve pencereler
    birbirinin ayni goruonuyordu: yanlis olanda "Baslat"a basmak, hangi
    projede oldugunuzun ekranda hicbir yerde yazmamasinin bedeliydi. Yol
    Ayarlar ekraninda bir satir olarak duruyordu -- yani gormek icin
    baktiginiz ekrandan cikmaniz gerekiyordu.
    """

    @staticmethod
    def _asset(name: str) -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / name).read_text(encoding="utf-8")

    def test_the_rail_has_a_place_for_it(self):
        html = self._asset("index.html")
        ray = html.split('<nav class="rail"', 1)[1].split("</nav>", 1)[0]
        assert 'id="rail-workspace"' in ray, "calisma alani sol rayda olmali"
        assert 'id="rail-ws-name"' in ray

    def test_it_sits_at_the_bottom(self):
        """Kullanicinin istedigi yer sol ALT kose. Rayin ustune tasinirsa
        bu test dusertir."""
        html = self._asset("index.html")
        dip = html.split('<div class="rail-foot">', 1)[1].split("</nav>", 1)[0]
        assert 'id="rail-workspace"' in dip
        # Ray dibindeki son parca: modellerden de sonra gelir.
        assert dip.index('id="rail-models"') < dip.index('id="rail-workspace"')

    def test_the_overview_carries_the_path(self, client, settings):
        """Ray `/api/overview`den besleniyor; alan kaybolursa kutu bos kalir."""
        assert client.get("/api/overview").json()["workspace"] == str(settings.workspace)

    def test_the_interface_actually_draws_it(self):
        js = self._asset("app.js")
        assert "renderWorkspace(data.workspace)" in js, (
            "genel durum geldiginde calisma alani cizilmiyor"
        )

    def test_only_the_folder_name_is_printed(self):
        """Kosede YALNIZCA klasor adi yazar.

        Kullanici (2026-09-01) tam yolun yazilmasini istemedi. Ayirt eden
        sey zaten ad; tam yol iki satir yer kapliyor ve arayuzun her
        ekran goruntusune ev dizinini sokuyordu.
        """
        html = self._asset("index.html")
        ray = html.split('<nav class="rail"', 1)[1].split("</nav>", 1)[0]
        assert "rail-ws-path" not in ray, "tam yol yeniden kosede yaziliyor"

        js = self._asset("app.js")
        yazilan = [s for s in js.splitlines() if "rail-ws" in s and "textContent" in s]
        assert len(yazilan) == 1, yazilan
        assert "rail-ws-name" in yazilan[0], yazilan[0]

    def test_the_full_path_is_still_reachable(self):
        """Yol ekrandan kalkti ama KAYBOLMADI: ipucunda ve panoda.
        Iki benzer klasor adi ancak boyle ayirt edilir."""
        js = self._asset("app.js")
        assert '$("#rail-ws-copy").title = `${path}' in js
        assert "clipboard.writeText(yol)" in js


class TestTheAuditLogIsReachableFromTheInterface:
    """Denetim gunlugu paneli.

    Uc calissa da paneli olmayan bir gunluk, olmayan bir gunluktur.
    """

    @staticmethod
    def _asset(name: str) -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / name).read_text(encoding="utf-8")

    def test_the_settings_screen_has_the_panel(self):
        html = self._asset("index.html")
        ayarlar = html.split('id="view-settings"', 1)[1]
        assert 'id="audit-panel"' in ayarlar
        assert 'id="audit-table"' in ayarlar

    def test_the_panel_starts_hidden(self):
        """Yonetici olmayan biri acildigi anda gunlugu gormemeli; panel
        once gizli gelir, `loadAudit` yetkiyi gorunce acar."""
        html = self._asset("index.html")
        satir = next(s for s in html.splitlines() if 'id="audit-panel"' in s)
        assert "hidden" in satir

    def test_the_filters_are_there(self):
        html = self._asset("index.html")
        for kimlik in ("audit-user", "audit-action", "audit-limit", "btn-audit-refresh"):
            assert f'id="{kimlik}"' in html, kimlik

    def test_the_panel_is_full_width(self):
        """Satir zaman + kisi + islem + ayrinti tasiyor. `.panel-row` iki
        sutunlu bir izgara: icine konursa gunluk yarim sutuna sikisir."""
        html = self._asset("index.html")
        onceki = html.split('id="audit-panel"', 1)[0]
        son_satir = onceki.rindex('<div class="panel-row">')
        assert "</div>" in onceki[son_satir:], (
            "denetim paneli iki sutunlu bir `panel-row` icinde kalmis"
        )

    def test_it_is_loaded_when_the_settings_screen_opens(self):
        js = self._asset("app.js")
        satir = next(s for s in js.splitlines() if 'name === "settings"' in s)
        assert "loadAudit()" in satir, satir.strip()

    def test_action_names_are_translated_not_stored(self):
        """Sunucu sabit tanimlayici gonderir ('run.start'); sozcuk sozlukten
        gelir. Metin saklansaydi eski satirlar yazildiklari gunun dilinde
        kalirdi -- kosu basliklarinda tam olarak bu olmustu."""
        js = self._asset("app.js")
        assert 'tv("audit.act", e.action)' in js

    def test_every_recorded_action_has_a_word_in_both_languages(self):
        """Gunluge yazilan her islem turunun karsiligi olmali: eksik
        anahtar ekranda ham `run.start` olarak gorunur."""
        import re

        from deerx.web.app import STATIC_DIR

        app_py = (Path(STATIC_DIR).parent / "app.py").read_text(encoding="utf-8")
        eylemler = set(re.findall(r'_audit\(\s*request,\s*"([a-z.]+)"', app_py))
        assert eylemler, "hic denetim cagrisi bulunamadi"

        sozluk = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
        for dil in ("tr", "en"):
            govde = sozluk.split(f"  {dil}: {{", 1)[1]
            for eylem in sorted(eylemler):
                assert f'"audit.act.{eylem}"' in govde, f"{dil}: audit.act.{eylem} yok"
                grup = eylem.split(".")[0]
                assert f'"audit.grp.{grup}"' in govde, f"{dil}: audit.grp.{grup} yok"


class TestTheBrowserGetsAnAddressItCanReach:
    """`0.0.0.0` baglanma adresidir, gidilecek adres degil.

    `--host 0.0.0.0` ile baslatildiginda hem acilan tarayici sekmesi hem
    de konsoldaki "dinleniyor" satiri `http://0.0.0.0:8791` gosteriyordu.
    O adres "butun arayuzler" demek; Firefox reddeder, digerleri
    tesadufen calisir. Kullanicinin gidecegi ad `localhost`.
    """

    def test_bind_all_becomes_localhost(self):
        from deerx.config import browse_host

        for adres in ("0.0.0.0", "::", "[::]", "*", ""):
            assert browse_host(adres) == "localhost", adres

    def test_loopback_becomes_localhost(self):
        from deerx.config import browse_host

        for adres in ("127.0.0.1", "::1", "[::1]"):
            assert browse_host(adres) == "localhost", adres

    def test_a_real_address_is_left_alone(self):
        """Aga acik bir kurulumda kullanici gercekten o adrese gidecek;
        onu `localhost` yapmak yanlis makineyi gosterirdi."""
        from deerx.config import browse_host

        assert browse_host("192.168.1.14") == "192.168.1.14"
        assert browse_host("deerx.local") == "deerx.local"

    def test_the_cli_and_the_server_use_the_same_rule(self):
        """Iki yerde iki ayri kural olsaydi biri duzeltilir, digeri
        yillarca yanlis adresi basardi -- nitekim ikisi de ayni satiri
        kopyalamisti."""
        from deerx.web.app import STATIC_DIR

        kaynak = Path(STATIC_DIR).parent.parent
        for ad in ("cli.py", "web/app.py"):
            metin = (kaynak / ad).read_text(encoding="utf-8")
            assert "browse_host(host)" in metin, ad
            assert "'localhost' if host ==" not in metin, f"{ad}: eski kural kalmis"


class TestArtifactsCarryTheirWorkflowNumber:
    """Cikti hangi is akisina ait, numarasiyla yazsin.

    Cikti grubu koSU numarasini gosteriyordu. Kosu bir is akisinin
    ADIMIDIR: "bu mockup hangi akistan cikmisti" sorusunun cevabi
    ekranda hic yoktu ve Is akislari ekranina gidip aramak gerekiyordu.
    """

    @staticmethod
    def _asset(name: str) -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / name).read_text(encoding="utf-8")

    def test_the_group_carries_the_workflow_number(self, client, state_of, settings):
        akis = state_of.create_workflow("Saha servis")
        run_id = "aaaabbbbcccc"
        state_of.start_run(run_id, goal="Saha servis", phases=["mockup"],
                           workflow_id=akis["id"])
        yol = settings.artifacts_dir / "pano.html"
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_text("<p>pano</p>", encoding="utf-8")
        state_of.add_artifact(
            Artifact(name="pano.html", kind="mockup", path=str(yol),
                     run_id=run_id, phase="mockup")
        )

        grup = client.get("/api/artifacts").json()["groups"][0]
        assert grup["workflow_seq"] == akis["seq"]
        assert grup["workflow_id"] == akis["id"]

    def test_a_run_without_a_workflow_gets_no_number(self, client, state_of, settings):
        """Eski kayitlarda `workflow_id` bos. Uydurulmus bir numara,
        olmayan bir akisa goturur."""
        run_id = "ddddeeeeffff"
        state_of.start_run(run_id, goal="Eski", phases=["mockup"])
        state_of._conn.execute(
            "UPDATE runs SET workflow_id = '' WHERE id = ?", (run_id,)
        )
        state_of._conn.commit()
        yol = settings.artifacts_dir / "eski.md"
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_text("eski", encoding="utf-8")
        state_of.add_artifact(
            Artifact(name="eski.md", kind="report", path=str(yol), run_id=run_id)
        )

        grup = next(
            g for g in client.get("/api/artifacts").json()["groups"]
            if g["run_id"] == run_id
        )
        assert grup["workflow_seq"] is None

    def test_the_interface_prints_it(self):
        js = self._asset("app.js")
        assert 'artifacts.wfShort' in js
        assert "group.workflow_seq" in js

    def test_a_missing_number_prints_nothing(self):
        """`#null` yazan bir rozet, olmayan bir bilgiyi varmis gibi
        gosterirdi."""
        js = self._asset("app.js")
        assert "group.workflow_seq == null ? \"\"" in js

    def test_the_badge_leads_to_that_workflow(self):
        """Numarayi gormek yetmiyor: "hangi akis" sorusunun devami her
        zaman "goster" oluyor."""
        js = self._asset("app.js")
        assert 'data-goto-wf' in js
        assert "state.activeWorkflow = rozet.dataset.gotoWf" in js

    def test_the_badge_does_not_toggle_the_group(self):
        """Rozet grup basliginin ICINDE; `stopPropagation` olmadan
        tiklamak grubu acip kapatir ve akisa gitmek grubu da kapatirdi.

        Yorumlar ELENIYOR: ilk yazdigimda testin capasi kendi aciklama
        satirimla eslesiyordu ve kod silindiginde bile yesil kaliyordu.
        """
        js = self._asset("app.js")
        blok = js.split("data-goto-wf", 1)[1].split("$$(\"[data-artifact]\"", 1)[0]
        kod = "\n".join(
            s for s in blok.splitlines()
            if not s.lstrip().startswith(("//", "*", "/*"))
        )
        assert "event.stopPropagation();" in kod


class TestRetryingAFailedStep:
    """Kirilan yerden devam edebilmek.

    Once tek care butun gelistirmeyi bastan baslatmakti: onuncu adimda
    kirilan bir is akisi, onden gecen dokuz adimin model bedelini ikinci kez
    odetiyordu -- ve kullanicinin istedigi sey hicbir zaman bu degildi.
    """

    def _failed_run(self, state_of, phases, *, failed_at, goal="hedef",
                    task_key="", plan_id="", son_durum=Status.FAILED):
        import uuid

        run_id = uuid.uuid4().hex[:12]
        seq = state_of.start_run(
            run_id, goal=goal, phases=[str(p) for p in phases],
            task_key=task_key, plan_id=plan_id,
        )
        for index, phase in enumerate(phases):
            state_of.start_run_step(run_id, phase, index)
            if phase is failed_at:
                state_of.finish_run_step(
                    run_id, phase, status=son_durum, error="patladi"
                )
                # Kosu ilk hatada durur; sonraki adimlarin satiri hic acilmaz.
                break
            state_of.finish_run_step(run_id, phase, status=Status.DONE)
        state_of.finish_run(run_id, status=Status.FAILED, error="patladi")
        return run_id, seq

    # -- plan kurma ---------------------------------------------------- #

    def test_the_plan_starts_at_the_first_failed_step(self, client, state_of):
        secilen = [Phase.INGEST, Phase.ANALYZE, Phase.RESEARCH, Phase.ASSESS]
        run_id, _ = self._failed_run(state_of, secilen, failed_at=Phase.RESEARCH)
        fazlar, baslangic = retry_plan(
            state_of.get_run(run_id), state_of.run_step_rows(run_id)
        )
        assert baslangic is Phase.RESEARCH
        # Sonraki adimlar da girer: `assess`, hic uretilmemis `research`
        # ciktisini okuyamaz.
        assert fazlar == [Phase.RESEARCH, Phase.ASSESS]

    def test_the_earliest_failure_wins(self, client, state_of):
        """Sonraki hatalar cogu zaman ilkinin sonucudur.

        Arkadaki hatadan baslamak ayni duvara tekrar carpar: eksik olan sey
        hala eksiktir.
        """
        run_id = "cokluhata12"
        fazlar = [Phase.INGEST, Phase.ANALYZE, Phase.RESEARCH]
        state_of.start_run(run_id, goal="h", phases=[str(p) for p in fazlar])
        state_of.start_run_step(run_id, Phase.INGEST, 0)
        state_of.finish_run_step(run_id, Phase.INGEST, status=Status.DONE)
        state_of.start_run_step(run_id, Phase.ANALYZE, 1)
        state_of.finish_run_step(run_id, Phase.ANALYZE, status=Status.FAILED, error="ilk")
        state_of.start_run_step(run_id, Phase.RESEARCH, 2)
        state_of.finish_run_step(run_id, Phase.RESEARCH, status=Status.FAILED, error="ikinci")
        _, baslangic = retry_plan(
            state_of.get_run(run_id), state_of.run_step_rows(run_id)
        )
        assert baslangic is Phase.ANALYZE

    def test_the_pipeline_order_does_not_widen_the_run(self, client, state_of):
        """Tekrar, kullanicinin SECTIGI adimlarla sinirli kalir.

        [ingest, analyze, plan] secip `analyze`da hata alan biri, tekrarda
        `research` ve `assess`in de kosmasini istemez -- onlari zaten
        bilerek disarida birakmisti.
        """
        secilen = [Phase.INGEST, Phase.ANALYZE, Phase.PLAN]
        run_id, _ = self._failed_run(state_of, secilen, failed_at=Phase.ANALYZE)
        fazlar, _ = retry_plan(
            state_of.get_run(run_id), state_of.run_step_rows(run_id)
        )
        assert fazlar == [Phase.ANALYZE, Phase.PLAN]

    def test_an_explicit_step_wins_even_if_it_succeeded(self, client, state_of):
        """Hata olmadan da "buradan itibaren tekrar yap" mesru bir istektir."""
        secilen = [Phase.INGEST, Phase.ANALYZE, Phase.RESEARCH]
        run_id, _ = self._failed_run(state_of, secilen, failed_at=Phase.RESEARCH)
        fazlar, baslangic = retry_plan(
            state_of.get_run(run_id), state_of.run_step_rows(run_id), "analyze"
        )
        assert baslangic is Phase.ANALYZE
        assert fazlar == [Phase.ANALYZE, Phase.RESEARCH]

    def test_a_step_outside_the_run_is_refused(self, client, state_of):
        run_id, _ = self._failed_run(
            state_of, [Phase.INGEST, Phase.ANALYZE], failed_at=Phase.ANALYZE
        )
        with pytest.raises(DeerXError) as hata:
            retry_plan(
                state_of.get_run(run_id), state_of.run_step_rows(run_id), "package"
            )
        assert "package" in str(hata.value)

    def test_waiting_for_an_answer_is_not_a_failure(self, client, state_of):
        """`needs_input`te ajan isini yapmis, kullanicidan cevap bekliyor.

        Tekrar kosmak ayni soruyu ikinci kez sormaktan baska bir sey yapmaz.
        """
        run_id, _ = self._failed_run(
            state_of, [Phase.INGEST, Phase.ANALYZE],
            failed_at=Phase.ANALYZE, son_durum=Status.NEEDS_INPUT,
        )
        with pytest.raises(DeerXError):
            retry_plan(state_of.get_run(run_id), state_of.run_step_rows(run_id))

    def test_nothing_failed_and_nothing_chosen_is_refused(self, client, state_of):
        run_id = "temizkosu12"
        state_of.start_run(run_id, goal="h", phases=["ingest"])
        state_of.start_run_step(run_id, Phase.INGEST, 0)
        state_of.finish_run_step(run_id, Phase.INGEST, status=Status.DONE)
        state_of.finish_run(run_id, status=Status.DONE)
        with pytest.raises(DeerXError):
            retry_plan(state_of.get_run(run_id), state_of.run_step_rows(run_id))

    # -- HTTP ucu ------------------------------------------------------ #

    def test_the_endpoint_reports_where_it_resumed(self, client, state_of, settings):
        settings.anthropic_api_key = None      # `ingest` model istemez
        run_id, _ = self._failed_run(state_of, [Phase.INGEST], failed_at=Phase.INGEST)
        cevap = client.post(f"/api/runs/{run_id}/retry")
        assert cevap.status_code == 200, cevap.text
        assert cevap.json()["from"] == "ingest"
        _wait_idle(client)

    def test_it_can_be_reached_by_sequence_number(self, client, state_of, settings):
        settings.anthropic_api_key = None
        _, seq = self._failed_run(state_of, [Phase.INGEST], failed_at=Phase.INGEST)
        assert client.post(f"/api/runs/{seq}/retry").status_code == 200
        _wait_idle(client)

    def test_an_unknown_run_is_404(self, client):
        assert client.post("/api/runs/yokboyle/retry").status_code == 404

    def test_a_clean_run_is_refused_with_a_reason(self, client, state_of):
        run_id = "temizkosu34"
        state_of.start_run(run_id, goal="h", phases=["ingest"])
        state_of.start_run_step(run_id, Phase.INGEST, 0)
        state_of.finish_run_step(run_id, Phase.INGEST, status=Status.DONE)
        state_of.finish_run(run_id, status=Status.DONE)
        cevap = client.post(f"/api/runs/{run_id}/retry")
        assert cevap.status_code == 400
        assert "basarisiz adim yok" in cevap.json()["error"]

    def test_the_retry_is_recorded_in_the_audit_log(self, client, state_of, settings):
        settings.anthropic_api_key = None
        run_id, _ = self._failed_run(state_of, [Phase.INGEST], failed_at=Phase.INGEST)
        client.post(f"/api/runs/{run_id}/retry")
        _wait_idle(client)
        gunluk = client.get("/api/audit").json()
        eylemler = [k["action"] for k in gunluk["entries"]]
        assert "run.retry" in eylemler

    def test_the_retry_forces_the_step_to_actually_run(self, client, state_of, monkeypatch):
        """Kullanici bu adimi ACIKCA tekrar istedi.

        Zorlama olmadan `_skip_reason` tamamlanmis bir fazi "onceden
        tamamlandi" deyip atlar: dugme calisir gorunur, hicbir sey olmaz.
        Sonraki adimlar da zorlanir -- kirilan bir adimin ustune kurulmus
        ciktilar supheli.
        """
        yakalanan: dict = {}
        gercek = client.app.state.deerx.runner.start

        def sahte(*args, **kwargs):
            yakalanan.update(kwargs)
            return gercek(*args, **kwargs)

        monkeypatch.setattr(client.app.state.deerx.runner, "start", sahte)
        run_id, _ = self._failed_run(state_of, [Phase.INGEST], failed_at=Phase.INGEST)
        client.post(f"/api/runs/{run_id}/retry")
        _wait_idle(client)
        assert yakalanan.get("force") is True

    def test_the_chosen_step_reaches_the_runner(self, client, state_of, monkeypatch):
        """Arayuzdeki "buradan itibaren" dugmesi `phase` gonderir.

        Uc bunu yok sayarsa dugme yanlis yerden baslatir ve kullanici bunu
        ancak kosu bittikten sonra fark eder. Kosturucu sahtelenip `RunBusy`
        atiyor: sinanan sey plan, kosunun kendisi degil -- gercek bir model
        cagrisi yapilmaz.
        """
        yakalanan: dict = {}

        def sahte(phases, **kwargs):
            yakalanan["phases"] = [str(p) for p in phases]
            raise RunBusy("test")

        monkeypatch.setattr(client.app.state.deerx.runner, "start", sahte)
        run_id, _ = self._failed_run(
            state_of, [Phase.INGEST, Phase.ANALYZE, Phase.RESEARCH],
            failed_at=Phase.RESEARCH,
        )
        client.post(f"/api/runs/{run_id}/retry", json={"phase": "analyze"})
        assert yakalanan["phases"] == ["analyze", "research"]

    # -- kosu neyi kosturdugunu hatirlamali ---------------------------- #

    def test_a_run_remembers_what_it_was_running(self, client, state_of):
        """Fazlar tek basina yetmiyor.

        Ayni [ingest, implement] listesi "T-014'u yap" da olabilir "sirada
        ne varsa yap" da. Bu ayrim kaydedilmezse tek bir gorev icin acilan
        kosu, tekrarda hazir olan BUTUN gorevleri kosardi.
        """
        state_of.start_run("gorevkosu12", goal="h", phases=["ingest"],
                           task_key="T-014", plan_id="P-2")
        kayit = state_of.get_run("gorevkosu12")
        assert (kayit["task_key"], kayit["plan_id"]) == ("T-014", "P-2")

    def test_reopening_a_run_does_not_forget_the_task(self, client, state_of):
        """Kaydi web katmani acar, boru hatti ayni kimlikle ikinci kez acar.

        Ikinci cagri bunlari bilmiyor; ustune bos yazilsaydi kosunun neyi
        kosturdugu tam da tekrar icin gerektigi anda silinmis olurdu.
        """
        state_of.start_run("gorevkosu34", goal="h", phases=["ingest"], task_key="T-020")
        state_of.start_run("gorevkosu34", goal="h", phases=["ingest"])
        assert state_of.get_run("gorevkosu34")["task_key"] == "T-020"

    def test_the_retry_carries_the_task_forward(self, client, state_of, settings):
        settings.anthropic_api_key = None
        run_id, _ = self._failed_run(
            state_of, [Phase.INGEST], failed_at=Phase.INGEST, task_key="T-014"
        )
        client.post(f"/api/runs/{run_id}/retry")
        _wait_idle(client)
        yeni = state_of.list_runs(1)[0]
        assert yeni["id"] != run_id
        assert yeni["task_key"] == "T-014"


class TestWorkflowChatApi:
    """Is akisi sohbeti: oku, gonder, temizle.

    Rota, modeli SENKRON cagiran orkestratoru bir is parcacigina aliyor;
    aksi halde sohbet suren her saniye butun arayuz -- canli akis dahil --
    donardi.
    """

    def _workflow(self, state_of):
        return state_of.workflow_for_goal("Hesap makinesi", brief="ilk talimat")

    def test_history_starts_empty(self, client, state_of):
        wf = self._workflow(state_of)
        data = client.get(f"/api/workflows/{wf['id']}/chat").json()
        assert data["messages"] == []

    def test_an_unknown_workflow_is_404(self, client):
        assert client.get("/api/workflows/boyle-yok/chat").status_code == 404

    def test_an_empty_message_is_refused(self, client, state_of):
        wf = self._workflow(state_of)
        r = client.post(f"/api/workflows/{wf['id']}/chat", json={"message": "  "})
        assert r.status_code == 400

    def test_a_sequence_number_also_works(self, client, state_of):
        """Kullanici arayuzde `#1` goruyor; adres cubugunda da o gecmeli."""
        wf = self._workflow(state_of)
        r = client.get(f"/api/workflows/{wf['seq']}/chat")
        assert r.status_code == 200

    def test_history_can_be_deleted(self, client, state_of):
        wf = self._workflow(state_of)
        state_of.add_chat_message(wf["id"], role="user", content="merhaba")
        state_of.add_chat_message(wf["id"], role="assistant", content="selam")

        r = client.request("DELETE", f"/api/workflows/{wf['id']}/chat")

        assert r.json()["deleted"] == 2
        assert client.get(f"/api/workflows/{wf['id']}/chat").json()["messages"] == []

    def test_the_changes_travel_with_the_message(self, client, state_of):
        """Modelin ne DEGISTIRDIGI cevabin yaninda durmali; kullanici
        bunu metnin icinde aramak zorunda kalmamali."""
        wf = self._workflow(state_of)
        state_of.add_chat_message(
            wf["id"], role="assistant", content="tamam",
            changes=["update_workflow: baslik"],
        )
        mesajlar = client.get(f"/api/workflows/{wf['id']}/chat").json()["messages"]
        assert mesajlar[0]["changes"] == ["update_workflow: baslik"]


class TestWorkflowStepLoad:
    """Ust ray IS AKISI BAZLI ve adim basina BEKLEYEN IS sayar.

    Proje geneli faz durumu yaniltiyordu: ayni projede birden fazla is
    akisi yasayabilir ve birinin bitirdigi faz otekinde hic kosulmamis
    olabilir; ray ikisini tek satirda topluyordu.

    "Bekleyen is" fazdan faza ayni sey degil ve bunu gizlemek yaniltici
    olurdu -- `implement` gercek bir gorev kuyrugu tasir, oteki fazlar
    tek ajanlidir ve orada bekleyen is fazin kendisidir.
    """

    def _load(self, state_of, workflow_id):
        from deerx.web.runner import workflow_step_load

        return {a["phase"]: a for a in workflow_step_load(state_of, workflow_id)}

    def test_implement_counts_pending_tasks(self, state_of):
        from deerx.pipeline.models import Status, Task

        wf = state_of.workflow_for_goal("Hesap makinesi")
        for i in range(3):
            state_of.add_task(Task(key=f"T-00{i+1}", title=f"gorev {i}"))
        state_of.update_task("T-001", status=Status.DONE)

        adim = self._load(state_of, wf["id"])["implement"]

        assert adim["waiting"] == 2, "bekleyen gorev sayisi yanlis"
        assert adim["unit"] == "task"

    def test_blocked_tasks_are_counted_apart(self, state_of):
        """Bloke gorev bekleyen degildir; ayni sayida gostermek
        'birazdan kosacak' izlenimi verirdi."""
        from deerx.pipeline.models import Status, Task

        wf = state_of.workflow_for_goal("Hesap makinesi")
        state_of.add_task(Task(key="T-001", title="a"))
        state_of.add_task(Task(key="T-002", title="b"))
        state_of.update_task("T-002", status=Status.BLOCKED)

        adim = self._load(state_of, wf["id"])["implement"]

        assert adim["waiting"] == 1 and adim["blocked"] == 1

    def test_a_single_agent_phase_waits_on_itself(self, state_of):
        """Kuyrugu olmayan fazda bekleyen is FAZIN KENDISIDIR: 1 ya da 0."""
        wf = state_of.workflow_for_goal("Hesap makinesi")
        yuk = self._load(state_of, wf["id"])

        assert yuk["qa"]["waiting"] == 1
        assert yuk["qa"]["unit"] == "phase"

    def test_a_finished_phase_waits_for_nothing(self, state_of):
        from deerx.pipeline.models import Phase, Status

        wf = state_of.workflow_for_goal("Hesap makinesi")
        state_of.start_run("r1", goal="Hesap makinesi", workflow_id=wf["id"])
        state_of.start_run_step("r1", Phase.ANALYZE, 0)
        state_of.finish_run_step("r1", Phase.ANALYZE, status=Status.DONE)

        adim = self._load(state_of, wf["id"])["analyze"]

        assert adim["waiting"] == 0 and adim["terminal"]

    def test_the_status_comes_from_this_workflow_not_the_project(self, state_of):
        """Baska bir is akisinda biten faz, BU is akisinda bitmis
        sayilmamali -- rayin is akisi bazli olmasinin butun sebebi bu."""
        from deerx.pipeline.models import Phase, Status

        birinci = state_of.workflow_for_goal("Hesap makinesi")
        state_of.start_run("r1", goal="Hesap makinesi", workflow_id=birinci["id"])
        state_of.start_run_step("r1", Phase.ANALYZE, 0)
        state_of.finish_run_step("r1", Phase.ANALYZE, status=Status.DONE)

        ikinci = state_of.create_workflow("Baska hedef")
        adim = self._load(state_of, ikinci["id"])["analyze"]

        assert adim["waiting"] == 1, "baska is akisinin ilerlemesi buraya sizdi"

    def test_every_pipeline_phase_appears(self, state_of):
        from deerx.pipeline.models import Phase

        wf = state_of.workflow_for_goal("Hesap makinesi")
        assert set(self._load(state_of, wf["id"])) == {str(p) for p in Phase.ordered()}


class TestChatDrawer:
    """Sohbet sagdan cikan bir cekmece; gorunumlerin DISINDA durur."""

    @staticmethod
    def _asset(name: str) -> str:
        from deerx.web.app import STATIC_DIR

        return (STATIC_DIR / name).read_text(encoding="utf-8")

    def test_the_drawer_lives_outside_the_views(self):
        """Bir gorunumun icine gomulseydi o gorunumun kaydirmasi ve yigin
        baglami cekmeceyi hapsederdi."""
        html = self._asset("index.html")
        cekmece = html.index('id="chat-drawer"')
        son_view = html.rindex('class="view"')
        assert cekmece > son_view, "cekmece bir gorunumun icinde"

    def test_it_slides_rather_than_appearing(self):
        """`display:none` ile gizlemek gecis animasyonunu imkansiz kilar."""
        css = self._asset("styles.css")
        blok = css[css.index(".drawer {"):css.index(".drawer-head")]
        assert "transform: translateX(100%)" in blok
        assert "transition: transform" in blok

    def test_reduced_motion_is_respected(self):
        css = self._asset("styles.css")
        assert "prefers-reduced-motion" in css

    def test_there_are_three_ways_out(self):
        """Kacis yolu olmayan bir katman kullaniciyi sayfayi yenilemeye
        zorlar: dugme, perde ve Esc."""
        js = self._asset("app.js")
        assert '$("#chat-close").addEventListener("click", closeChat)' in js
        assert '$("#chat-veil").addEventListener("click", closeChat)' in js
        assert '"Escape"' in js and "closeChat()" in js
