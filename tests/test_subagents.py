"""Alt ajan: isi bolmek, dar arac kumesi, temiz baglam.

Kullanicinin adiyla istedigi sey: "yeri geldiginde var olan llm de alt
agent planlayip calistirabilmeli". Buradaki testler uc seyi kilitliyor --
alt ajanin gercekten kostugunu, ozyinelemenin durdugunu ve ebeveynin
onaylarinin devralinmadigini.

Hicbiri gercek model cagirmaz.
"""

from __future__ import annotations

import pytest

from deerx.tools import SUBAGENT_ROLES, TOOLSETS, build_registry
from deerx.tools.agents import MAX_DEPTH


class SahteSonuc:
    """`AgentResult`in testte gereken yuzu."""

    def __init__(self, text: str = "tamam", error: str | None = None) -> None:
        self.text = text
        self.error = error
        self.iterations = 3


class TestPlanlama:
    def test_planning_does_not_run_anything(self, ctx):
        """`plan_subagents` bir PLAN doner; hicbirini calistirmaz.

        Planlamayi calistirmaktan ayirmak, isi bolmenin gercekten gerekip
        gerekmedigini once yazili gormeyi saglar.
        """
        cagrildi: list[str] = []
        ctx.spawn = lambda *a: cagrildi.append(a[0]) or SahteSonuc()

        sonuc = build_registry().execute(
            "plan_subagents",
            {"tasks": [
                {"role": "researcher", "task": "surum bul", "deliverable": "surum no"},
                {"role": "qa", "task": "testi kos", "deliverable": "sonuc"},
            ]},
            ctx,
        )
        assert not sonuc.is_error
        assert cagrildi == [], "planlama alt ajan calistirdi"
        assert "researcher" in sonuc.content and "qa" in sonuc.content

    def test_a_part_without_a_deliverable_is_refused(self, ctx):
        """Teslim beklentisi olmayan alt ajan, ne dondurdugu belirsiz bir
        metin doner."""
        sonuc = build_registry().execute(
            "plan_subagents",
            {"tasks": [{"role": "qa", "task": "bir sey yap", "deliverable": "  "}]},
            ctx,
        )
        assert sonuc.is_error

    def test_a_pipeline_role_is_not_a_subagent_role(self, ctx):
        """Boru hattini yuruten roller bilerek disarida: onlar bir fazin
        sahibi ve kendi ciktilarini uretiyorlar."""
        assert "analyst" not in SUBAGENT_ROLES
        assert "architect" not in SUBAGENT_ROLES
        sonuc = build_registry().execute(
            "plan_subagents",
            {"tasks": [{"role": "analyst", "task": "x", "deliverable": "y"}]},
            ctx,
        )
        assert sonuc.is_error


class TestCalistirma:
    def test_the_subagent_actually_runs_and_its_text_comes_back(self, ctx):
        cagrilar: list[tuple] = []

        def sahte(role, task, context):
            cagrilar.append((role, task, context))
            return SahteSonuc(text="qwen3.8 max, 2024-11 surumu")

        ctx.spawn = sahte
        sonuc = build_registry().execute(
            "run_subagent",
            {"role": "researcher", "task": "surumu bul", "deliverable": "surum no"},
            ctx,
        )
        assert not sonuc.is_error
        assert "qwen3.8 max" in sonuc.content
        assert len(cagrilar) == 1
        # Teslim beklentisi goreve KATILIR: alt ajan cevabini ona gore
        # bicimlendirsin.
        assert "surum no" in cagrilar[0][1]

    def test_a_failing_subagent_is_reported_not_swallowed(self, ctx):
        ctx.spawn = lambda *a: SahteSonuc(text="", error="model dustu")
        sonuc = build_registry().execute(
            "run_subagent",
            {"role": "qa", "task": "kos", "deliverable": "sonuc"},
            ctx,
        )
        assert sonuc.is_error and "model dustu" in sonuc.content

    def test_without_a_spawner_it_refuses_instead_of_pretending(self, ctx):
        """Sohbet ve test baglaminda calistirici yok. Sessizce basarili
        donmek, modelin isini yaptigini sanmasina yol acardi."""
        assert ctx.spawn is None
        sonuc = build_registry().execute(
            "run_subagent",
            {"role": "qa", "task": "kos", "deliverable": "sonuc"},
            ctx,
        )
        assert sonuc.is_error


class TestOzyineleme:
    def test_a_subagent_cannot_run_subagents(self, ctx):
        """Sinirsiz derinlik, tek bir istegin butun butceyi harcayacagi ve
        nerede durdugunu kimsenin goremeyecegi bir agac uretir."""
        ctx.spawn = lambda *a: SahteSonuc()
        cocuk = ctx.child()
        assert cocuk.depth == MAX_DEPTH

        sonuc = build_registry().execute(
            "run_subagent",
            {"role": "qa", "task": "kos", "deliverable": "sonuc"},
            cocuk,
        )
        assert sonuc.is_error


class TestTuretilmisBaglam:
    def test_the_parents_approvals_are_not_inherited(self, ctx):
        """OLCULDU BIR YETKI SIZINTISI OLURDU.

        Kullanici "su tehlikeli komutu calistir" dediginde O KOMUTA onay
        verdi, bir role degil. Onay kumesini alt ajana tasimak, ebeveynin
        aldigi izni cocugun sessizce kullanmasi demek.
        """
        ctx._granted.add("rm -rf build")  # noqa: SLF001 - kasitli
        cocuk = ctx.child()
        assert cocuk._granted == set()  # noqa: SLF001

    def test_the_failed_address_counter_is_not_inherited(self, ctx):
        """Alt ajanin hic denemedigi bir adresi 'cok denedin' diye
        reddetmesi yanlis olurdu."""
        ctx._failed_fetches["http://olu"] = 9  # noqa: SLF001
        assert ctx.child()._failed_fetches == {}  # noqa: SLF001

    def test_shared_resources_are_passed_through(self, ctx):
        """Alt ajan AYNI projede calisiyor: ikinci bir tarayici acmak ya
        da ikinci bir konteyner kurmak anlamsiz olurdu."""
        cocuk = ctx.child()
        assert cocuk.settings is ctx.settings
        assert cocuk.kb is ctx.kb
        assert cocuk.state is ctx.state
        assert cocuk.events is ctx.events

    def test_the_document_scope_can_be_narrowed_but_never_widens(self, ctx):
        ctx.doc_scope = ("a.md", "b.md")
        assert ctx.child().doc_scope == ("a.md", "b.md")
        assert ctx.child(doc_scope=("a.md",)).doc_scope == ("a.md",)


class TestRolKumeleri:
    def test_only_broad_roles_can_split_work(self):
        """Butun rollere vermek, her ajanin her isi bolmeye calismasi
        demek olurdu; bolmek bir maliyettir."""
        bolebilen = {r for r, araclar in TOOLSETS.items() if "run_subagent" in araclar}
        assert bolebilen == {
            "architect", "planner", "backend", "frontend", "qa", "reviewer"
        }

    def test_the_summarizer_exists_and_cannot_write(self):
        """`ROLE_TIERS` `fast` katmanini tanimliyordu ama karsiligi olan
        bir rol YOKTU: ucuz model katmani hic kullanilmiyordu."""
        from deerx.agents.prompts import ROLES
        from deerx.config import ROLE_TIERS

        assert "summarizer" in ROLES
        assert "summarizer" in TOOLSETS
        assert ROLE_TIERS.get("summarizer") == "fast"

        yazma = {"write_file", "edit_file", "run_command", "start_service"}
        assert not (set(TOOLSETS["summarizer"]) & yazma)

    def test_a_subagent_never_sees_the_subagent_tools(self, settings, ctx):
        """Kisit ROLE degil DERINLIGE bagli.

        `qa` bir fazin sahibi olarak isi bolebilmeli ama bir alt ajan
        olarak cagrildiginda bolememeli -- ayni rol, farkli baglam.
        Reddedilen bir araci modele once gosterip sonra geri almak, o
        turu bosa harcamak demektir.
        """
        from deerx.agents import build_agent

        class SahteIstemci:
            total_cost = 0.0

        for rol in ("qa", "reviewer"):
            ebeveyn = build_agent(
                rol, settings=settings, client=SahteIstemci(),
                registry=build_registry(), context=ctx, events=ctx.events,
                stream=False,
            )
            cocuk = build_agent(
                rol, settings=settings, client=SahteIstemci(),
                registry=build_registry(), context=ctx.child(),
                events=ctx.events, stream=False,
            )
            ebeveyn_araclari = {a["name"] for a in ebeveyn.registry.specs()}
            cocuk_araclari = {a["name"] for a in cocuk.registry.specs()}
            assert "run_subagent" in ebeveyn_araclari, rol
            assert "run_subagent" not in cocuk_araclari, rol


@pytest.mark.parametrize("rol", SUBAGENT_ROLES)
def test_every_subagent_role_can_actually_be_built(rol, settings, ctx):
    """Rol listesinde olup arac kumesi ya da istemi olmayan bir rol,
    yalnizca CALISMA ZAMANINDA patlardi."""
    from deerx.agents import build_agent

    class SahteIstemci:
        total_cost = 0.0

    ajan = build_agent(
        rol, settings=settings, client=SahteIstemci(), registry=build_registry(),
        context=ctx, events=ctx.events, stream=False,
    )
    assert ajan.system_prompt.strip()
