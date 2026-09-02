"""Python tarafinin mesaj katalogu.

Arayuz metinleri `static/i18n.js` icinde cift dilliydi ama kullanicinin
gordugu her sey oradan gelmiyor: canli olay akisi, arac hatalari ve CLI
ciktisi Python'dan geliyor ve dilden bagimsiz Turkce'ydi. Ingilizce secili
bir arayuzde akis Turkce akiyordu.
"""

from __future__ import annotations

import re
import string

import pytest

from deerx.i18n import CATALOG, SUPPORTED, language, set_language, t


@pytest.fixture(autouse=True)
def _reset_language():
    onceki = language()
    yield
    set_language(onceki)


class TestCatalogShape:
    def test_both_languages_cover_every_key(self):
        """Bir dilde olup digerinde olmayan anahtar, sessiz bir bosluktur."""
        for key, entry in CATALOG.items():
            eksik = [d for d in SUPPORTED if not entry.get(d)]
            assert not eksik, f"{key}: {eksik} eksik"

    def test_placeholders_match_between_languages(self):
        """Ceviride kaybolan bir yer tutucu, kullaniciya eksik bilgi verir."""
        for key, entry in CATALOG.items():
            kumeler = {
                dil: {
                    alan
                    for _, alan, _, _ in string.Formatter().parse(entry[dil])
                    if alan
                }
                for dil in SUPPORTED
            }
            assert len(set(map(frozenset, kumeler.values()))) == 1, f"{key}: {kumeler}"

    def test_no_turkish_letters_in_english(self):
        """Ingilizce metinde Turkce harf, yarim kalmis ceviri isaretidir."""
        turkce = set("ıİşŞğĞçÇöÖüÜ")
        for key, entry in CATALOG.items():
            kalanlar = turkce & set(entry["en"])
            assert not kalanlar, f"{key}: {sorted(kalanlar)}"

    def test_keys_are_namespaced(self):
        for key in CATALOG:
            assert re.fullmatch(r"[a-z_]+\.[a-z_]+", key), key


class TestLookup:
    def test_the_active_language_is_used(self):
        set_language("en")
        assert t("service.list_empty") == "No service is running."
        set_language("tr")
        assert t("service.list_empty") == "Calisan servis yok."

    def test_an_unknown_language_falls_back_to_turkish(self):
        set_language("de")
        assert language() == "tr"

    def test_values_are_substituted(self):
        set_language("en")
        assert "web" in t("service.started", name="web", command="npm run dev")

    def test_a_missing_key_returns_itself(self):
        """Eksik ceviri gorunur olmali ama hicbir sey cokmemeli."""
        assert t("boyle.bir.anahtar.yok") == "boyle.bir.anahtar.yok"

    def test_a_missing_value_does_not_lose_the_message(self):
        """Eksik bir degisken yuzunden kullaniciya hic mesaj gostermemektense
        ham sablonu gostermek iyidir."""
        sonuc = t("service.started", name="web")   # `command` verilmedi
        assert "{command}" in sonuc or "web" in sonuc
        assert sonuc != ""


class TestMessagesActuallySwitch:
    """Dili degistirmek gercek mesajlari degistirmeli.

    Katalogun dogru olmasi yetmez: cagri yerleri hala sabit Turkce metin
    tasiyor olabilir ve kimse fark etmez.
    """

    def test_a_tool_error_speaks_the_chosen_language(self, ctx):
        from deerx.errors import ToolError
        from deerx.tools import build_registry

        ctx.settings.shell.enabled = False
        arac = build_registry().get("run_command")

        set_language("en")
        with pytest.raises(ToolError) as ing:
            arac.run(ctx, command="ls")
        set_language("tr")
        with pytest.raises(ToolError) as tur:
            arac.run(ctx, command="ls")

        assert "Shell access is off" in str(ing.value)
        assert "Kabuk erisimi kapali" in str(tur.value)

    def test_a_record_error_speaks_the_chosen_language(self, ctx):
        from deerx.errors import ToolError
        from deerx.tools import build_registry

        arac = build_registry().get("record_gaps")
        set_language("en")
        with pytest.raises(ToolError, match="required"):
            arac.run(ctx, items=[{"key": "GAP-001"}])
        set_language("tr")
        with pytest.raises(ToolError, match="zorunlu"):
            arac.run(ctx, items=[{"key": "GAP-001"}])

    def test_the_settings_object_drives_the_language(self, settings):
        """Ayari degistirmek yeterli olmali; ayrica cagri gerekmemeli."""
        from deerx.config import Settings

        Settings(workspace=settings.workspace, language="en")
        assert language() == "en"
        Settings(workspace=settings.workspace, language="tr")
        assert language() == "tr"

    def test_the_agent_hints_are_translated(self):
        """Modele giden yonlendirmeler de dili takip etmeli."""
        set_language("en")
        assert "TURN BUDGET" in t("agent.budget_hint", left=3, total=24)
        assert "CUT OFF" in t("agent.truncated_hint")
        set_language("tr")
        assert "TUR BUTCESI" in t("agent.budget_hint", left=3, total=24)
        assert "KESILDI" in t("agent.truncated_hint")


class TestCliLanguage:
    """CLI yardimi da dili takip etmeli.

    Yardim metinleri Typer dekoratorlerinde, yani ice aktarma aninda
    hesaplanir. `Settings` o an henuz yuklenmemistir; bu yuzden dil
    `cli._early_language` ile daha once, hafif bir okumayla belirlenir.
    Bu sinif o zincirin ucundan tutar: ortam degiskeni ve `deerx.toml`
    gercekten yardim metnini degistiriyor mu?
    """

    def test_the_environment_variable_wins(self, monkeypatch):
        from deerx.cli import _early_language

        monkeypatch.setenv("DEERX_LANGUAGE", "en")
        assert _early_language() == "en"

    def test_the_workspace_config_is_read(self, tmp_path, monkeypatch):
        from deerx.cli import _early_language

        monkeypatch.delenv("DEERX_LANGUAGE", raising=False)
        monkeypatch.delenv("DEERX_LANG", raising=False)
        (tmp_path / "deerx.toml").write_text(
            '[deerx]\nlanguage = "en"\n', encoding="utf-8"
        )
        monkeypatch.setenv("DEERX_WORKSPACE", str(tmp_path))
        assert _early_language() == "en"

    def test_a_broken_config_does_not_break_the_cli(self, tmp_path, monkeypatch):
        """Bozuk yapilandirma yuzunden CLI ice aktarilamaz hale gelirse,
        kullanicinin hatayi duzeltmek icin calistiracagi komut da calismaz."""
        from deerx.cli import _early_language

        monkeypatch.delenv("DEERX_LANGUAGE", raising=False)
        monkeypatch.delenv("DEERX_LANG", raising=False)
        (tmp_path / "deerx.toml").write_text("bu = gecerli toml degil [", encoding="utf-8")
        monkeypatch.setenv("DEERX_WORKSPACE", str(tmp_path))
        assert _early_language() == "tr"

    @pytest.mark.parametrize(
        ("dil", "beklenen"),
        [
            ("en", "Runs a hybrid search over the knowledge base."),
            ("tr", "Bilgi tabaninda hibrit arama yapar."),
        ],
    )
    def test_help_output_switches(self, dil, beklenen, tmp_path):
        """Ayri bir surecte: dekoratorler o dille kuruluyor mu?

        Ice aktarma anina bagli oldugu icin bu tek basina alt surecte
        olculebilir; ayni surecte modul zaten yuklenmis olur.
        """
        import os
        import subprocess
        import sys

        ortam = {
            **os.environ,
            "DEERX_LANGUAGE": dil,
            "COLUMNS": "200",
            "PYTHONIOENCODING": "utf-8",
        }
        sonuc = subprocess.run(
            [sys.executable, "-m", "deerx.cli", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=ortam,
            cwd=tmp_path,
            timeout=120,
        )
        assert sonuc.returncode == 0, sonuc.stderr
        assert beklenen in sonuc.stdout


class TestPhaseLabels:
    """Faz adlari iki katalogda birden yasiyor.

    Arayuz faz adini istemci tarafinda cozer (`static/i18n.js`), CLI
    Python tarafinda. Ayni anahtarlar iki dosyada oldugu icin biri
    guncellenip digeri unutulabilir: yeni bir faz eklendiginde CLI
    "phase.deploy" gibi ham bir anahtar basardi ve kimse fark etmezdi.
    """

    def test_every_phase_has_a_label_in_python(self):
        from deerx.pipeline import Phase

        for faz in Phase.ordered():
            for onek in ("phase", "agent", "produces"):
                anahtar = f"{onek}.{faz.value}"
                assert anahtar in CATALOG, anahtar

    def test_every_phase_has_a_label_in_the_browser_catalog(self):
        import pathlib

        from deerx.pipeline import Phase

        js = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src" / "deerx" / "web" / "static" / "i18n.js"
        ).read_text(encoding="utf-8")
        for faz in Phase.ordered():
            for onek in ("phase", "agent", "produces"):
                anahtar = f'"{onek}.{faz.value}"'
                # Bir kez TR, bir kez EN blogunda.
                assert js.count(anahtar) == 2, f"{anahtar}: {js.count(anahtar)} gecis"

    def test_the_labels_actually_switch(self):
        from deerx.pipeline import Phase

        set_language("en")
        assert Phase.INGEST.label == "Ingest"
        assert Phase.REVIEW.agent_label == "Reviewer"
        set_language("tr")
        assert Phase.INGEST.label == "Doküman alımı"
        assert Phase.REVIEW.agent_label == "İnceleyici"

    def test_the_stage_id_does_not_translate(self):
        """`stage` bir kimlik: arayuz onu `t("stage." + ad)` diye ariyor.
        Cevrilseydi Ingilizce arayuzde anahtarin kendisi gorunurdu."""
        from deerx.pipeline import Phase

        set_language("en")
        ingilizce = [p.stage for p in Phase.ordered()]
        set_language("tr")
        assert ingilizce == [p.stage for p in Phase.ordered()]
