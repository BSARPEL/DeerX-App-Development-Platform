"""MCP sunucusu testleri.

Iki katman dogrulanir:
  1. Arac/kaynak kaydi ve is mantigi (surec ici).
  2. Gercek stdio el sikismasi (alt surec) — stdout'a sizan tek bir satir
     JSON-RPC akisini bozar, bunu ancak gercek tasima katmani yakalar.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


def _text(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_server(workspace: Path, monkeypatch):
    """Sunucu modulunu izole bir calisma alanina baglar."""
    monkeypatch.setenv("DEERX_WORKSPACE", str(workspace))
    (workspace / "deerx.toml").write_text(
        '[deerx]\napproval_mode = "auto"\n\n'
        '[deerx.rag]\nembedding_provider = "hash"\nembedding_dim = 128\n',
        encoding="utf-8",
    )
    import deerx.mcp_server.server as server

    server._orchestrator = None  # onceki testten kalan ornegi temizle
    yield server
    if server._orchestrator is not None:
        server._orchestrator.close()
        server._orchestrator = None


class TestToolSurface:
    async def test_expected_tools_are_registered(self, mcp_server):
        names = {t.name for t in await mcp_server.mcp.list_tools()}
        assert names == {
            "deerx_ingest", "deerx_search", "deerx_documents", "deerx_status",
            "deerx_state", "deerx_tasks", "deerx_update_task", "deerx_artifact",
            "deerx_run_phase", "deerx_next_task",
            "deerx_questions", "deerx_answer", "deerx_skip_question",
            "deerx_package",
        }

    async def test_every_tool_has_description_and_schema(self, mcp_server):
        for tool in await mcp_server.mcp.list_tools():
            assert tool.description, f"{tool.name} aciklamasiz"
            assert tool.input_schema["type"] == "object"

    async def test_resources_are_registered(self, mcp_server):
        uris = {str(r.uri) for r in await mcp_server.mcp.list_resources()}
        templates = {t.uri_template for t in await mcp_server.mcp.list_resource_templates()}
        assert "deerx://state" in uris
        assert "deerx://artifacts/{name}" in templates


class TestToolBehaviour:
    async def test_ingest_then_search(self, mcp_server, workspace):
        ingested = await mcp_server.mcp.call_tool("deerx_ingest", {"path": "docs"})
        assert '"indexed_files": 1' in _text(ingested)

        found = await mcp_server.mcp.call_tool(
            "deerx_search", {"query": "cevrimdisi calisma", "k": 2}
        )
        assert "Cevrimdisi" in _text(found)

    async def test_search_on_empty_base_explains_itself(self, mcp_server):
        result = await mcp_server.mcp.call_tool("deerx_search", {"query": "herhangi bir sey"})
        assert "sonuc yok" in _text(result).lower()

    async def test_status_reports_phases(self, mcp_server):
        text = _text(await mcp_server.mcp.call_tool("deerx_status", {}))
        assert '"phase": "ingest"' in text
        assert '"knowledge_base"' in text

    async def test_unknown_phase_is_rejected(self, mcp_server):
        text = _text(await mcp_server.mcp.call_tool("deerx_run_phase", {"phase": "yok"}))
        assert "bilinmeyen faz" in text.lower()

    async def test_unknown_task_update_is_rejected(self, mcp_server):
        text = _text(
            await mcp_server.mcp.call_tool(
                "deerx_update_task", {"key": "T-999", "status": "done"}
            )
        )
        assert "T-999" in text and "yok" in text

    async def test_task_roundtrip(self, mcp_server):
        from deerx.pipeline.models import Status, Task

        orch = mcp_server._get()
        orch.state.add_task(Task(key="T-001", title="ilk gorev"))

        nxt = _text(await mcp_server.mcp.call_tool("deerx_next_task", {}))
        assert "T-001" in nxt
        # next_task durumu DEGISTIRMEZ: disaridaki ajan isi ustlenene kadar bekler.
        assert orch.state.get_task("T-001").status == Status.PENDING

        await mcp_server.mcp.call_tool(
            "deerx_update_task", {"key": "T-001", "status": "done", "result": "bitti"}
        )
        assert orch.state.get_task("T-001").status == Status.DONE

    async def test_question_answer_flow(self, mcp_server):
        """Disaridaki ajan soruyu okur, kullaniciya iletir, cevabini kaydeder."""
        from deerx.pipeline.models import Question

        orch = mcp_server._get()
        orch.state.add_question(
            Question(key="Q-001", question="Hangi ERP kullaniliyor?", blocking=True)
        )

        listed = _text(await mcp_server.mcp.call_tool("deerx_questions", {}))
        assert "Hangi ERP kullaniliyor?" in listed
        assert '"blocking_open"' in listed

        answered = _text(
            await mcp_server.mcp.call_tool(
                "deerx_answer", {"key": "q-001", "answer": "Logo Tiger 3"}
            )
        )
        assert '"remaining_blocking": []' in answered
        assert orch.state.get_question("Q-001").answer == "Logo Tiger 3"

    async def test_empty_answer_is_rejected(self, mcp_server):
        from deerx.pipeline.models import Question

        mcp_server._get().state.add_question(Question(key="Q-001", question="x"))
        text = _text(
            await mcp_server.mcp.call_tool("deerx_answer", {"key": "Q-001", "answer": "   "})
        )
        assert "bos cevap" in text.lower()

    async def test_skip_records_assumption(self, mcp_server):
        from deerx.pipeline.models import Question

        orch = mcp_server._get()
        orch.state.add_question(Question(key="Q-001", question="Renk?", blocking=True))
        text = _text(
            await mcp_server.mcp.call_tool(
                "deerx_skip_question", {"key": "Q-001", "assumption": "marka mavisi"}
            )
        )
        assert "marka mavisi" in text
        assert orch.state.get_question("Q-001").status == "skipped"

    async def test_run_phase_reports_gate(self, mcp_server):
        """Kapi kapaliyken faz calistirilmaz; disaridaki ajana durum bildirilir."""
        from deerx.pipeline.models import Question

        orch = mcp_server._get()
        orch.state.add_question(Question(key="Q-001", question="Butce?", blocking=True))
        text = _text(
            await mcp_server.mcp.call_tool("deerx_run_phase", {"phase": "ingest"})
        )
        assert '"status": "needs_input"' in text
        assert "Q-001" in text
        assert "deerx_answer" in text

    async def test_artifact_listing_and_read(self, mcp_server):
        from deerx.pipeline.models import Artifact

        orch = mcp_server._get()
        path = orch.settings.artifacts_dir / "rapor.md"
        path.write_text("# Rapor\nicerik", encoding="utf-8")
        orch.state.add_artifact(
            Artifact(name="rapor.md", kind="report", path=str(path), summary="ozet")
        )

        listing = _text(await mcp_server.mcp.call_tool("deerx_artifact", {}))
        assert "rapor.md" in listing

        content = _text(await mcp_server.mcp.call_tool("deerx_artifact", {"name": "rapor.md"}))
        assert content.startswith("# Rapor")

        missing = _text(await mcp_server.mcp.call_tool("deerx_artifact", {"name": "yok.md"}))
        assert "bulunamadi" in missing

    async def test_package_artifact_returns_the_report_not_raw_bytes(self, mcp_server):
        """Zip'i utf-8 diye okumak UnicodeDecodeError verirdi."""
        from deerx.pipeline.models import Status, Task

        orch = mcp_server._get()
        (orch.settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        orch.state.add_task(Task(key="T-001", title="Kur", status=Status.DONE))

        built = _text(await mcp_server.mcp.call_tool("deerx_package", {}))
        assert '"ready": true' in built.lower()

        name = next(a.name for a in orch.state.list_artifacts() if a.kind == "package")
        content = _text(await mcp_server.mcp.call_tool("deerx_artifact", {"name": name}))
        assert content.startswith(f"# {name}")
        assert "Teslimat paketi" in content
        assert "## Paket icerigi" in content

    async def test_package_is_registered_exactly_once(self, mcp_server):
        from deerx.pipeline.models import Status, Task

        orch = mcp_server._get()
        (orch.settings.workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
        orch.state.add_task(Task(key="T-001", title="Kur", status=Status.DONE))
        await mcp_server.mcp.call_tool("deerx_package", {})

        packages = [a for a in orch.state.list_artifacts() if a.kind == "package"]
        assert len(packages) == 1
        # Ozette hem dosya sayisi hem boyut olmali (cift kayit ozeti bozuyordu).
        assert "dosya" in packages[0].summary and "MB" in packages[0].summary


class TestStdioTransport:
    """Gercek alt surecte JSON-RPC el sikismasi."""

    async def test_handshake_and_tool_call(self, workspace: Path):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        (workspace / "deerx.toml").write_text(
            '[deerx]\napproval_mode = "auto"\n\n'
            '[deerx.rag]\nembedding_provider = "hash"\nembedding_dim = 128\n',
            encoding="utf-8",
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deerx.mcp_server"],
            env={**os.environ, "DEERX_WORKSPACE": str(workspace)},
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} >= {"deerx_search", "deerx_status"}

                # Sunucu konsola bir sey yazsaydi bu cagri protokol hatasi verirdi.
                ingest = await session.call_tool("deerx_ingest", {"path": "docs"})
                assert not ingest.is_error
                assert '"indexed_files": 1' in ingest.content[0].text

                found = await session.call_tool(
                    "deerx_search", {"query": "is emri yasam dongusu", "k": 2}
                )
                assert "Is emri" in found.content[0].text
