"""Model saglayicilari ve ayarlar ekrani."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest

from deerx.config import DEFAULT_PORT, Settings
from deerx.llm.providers import BY_KEY, PRESETS, catalog, preset_for
from deerx.web.app import STATIC_DIR


def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


class TestProviderCatalog:
    """Adresler hafizadan degil olcumden geliyor.

    Listedeki her uce anahtarsiz bir istek atildi ve ucun var olup kimlik
    istedigi dogrulandi. Bu testler yapinin bozulmamasini korur; adreslerin
    canliligini sinamak agi teste sokmak olurdu.
    """

    def test_every_preset_is_complete(self):
        for preset in PRESETS:
            assert preset.key and preset.label, preset
            assert preset.protocol in ("openai", "anthropic"), preset.key

    def test_keys_are_unique(self):
        keys = [p.key for p in PRESETS]
        assert len(keys) == len(set(keys))

    def test_remote_endpoints_use_https(self):
        """Uzak bir uce anahtar duz metin gonderilmemeli."""
        for preset in PRESETS:
            if not preset.base_url or preset.local:
                continue
            assert urlparse(preset.base_url).scheme == "https", preset.key

    def test_local_endpoints_are_loopback(self):
        """'Yerel' etiketi gercekten yerel olmali."""
        for preset in PRESETS:
            if not preset.local:
                continue
            host = urlparse(preset.base_url or "").hostname
            assert host in ("127.0.0.1", "localhost"), preset.key

    def test_no_model_names_are_hardcoded(self):
        """Model adlari kodda TUTULMAZ.

        Saglayicilar onlari sik degistirir; birkac ay eski bir liste
        kullaniciya var olmayan bir modeli onerir. Dogru kaynak ucun
        kendi `/models` listesi.
        """
        source = (STATIC_DIR.parent.parent / "llm" / "providers.py").read_text(encoding="utf-8")
        for suspicious in ("gpt-4", "gpt-5", "claude-3", "claude-opus", "llama-3",
                           "gemini-1.5", "gemini-2", "mixtral", "deepseek-chat"):
            assert suspicious not in source.lower(), f"model adi gomulu: {suspicious}"

    def test_anthropic_uses_its_own_protocol(self):
        assert BY_KEY["anthropic"].protocol == "anthropic"
        assert BY_KEY["anthropic"].base_url is None, "kendi istemcisi adresi biliyor"

    def test_everyone_else_speaks_openai(self):
        others = [p for p in PRESETS if p.key != "anthropic"]
        assert others and all(p.protocol == "openai" for p in others)

    def test_custom_option_exists_and_keeps_the_address(self):
        """Listede olmayan bir uc icin elle giris sart."""
        custom = BY_KEY["custom"]
        assert custom.base_url is None, "elle secenek adresi ustune yazmamali"

    @pytest.mark.parametrize("key", ["groq", "openrouter", "openai", "anthropic", "vllm"])
    def test_catalog_is_serialisable(self, key):
        rows = {row["key"]: row for row in catalog()}
        assert key in rows
        assert set(rows[key]) >= {"key", "label", "protocol", "base_url", "local"}


class TestPresetDetection:
    """Ayarlar acildiginda hangi saglayici secili gorunecek."""

    def test_matches_a_known_endpoint(self):
        assert preset_for("https://api.groq.com/openai/v1", "openai") == "groq"

    def test_ignores_a_trailing_slash(self):
        assert preset_for("https://api.groq.com/openai/v1/", "openai") == "groq"

    def test_anthropic_wins_on_protocol(self):
        """Anthropic'te adres alani anlamsiz; protokol karar verir."""
        assert preset_for("https://api.groq.com/openai/v1", "anthropic") == "anthropic"

    def test_unknown_endpoint_falls_back_to_custom(self):
        assert preset_for("https://kendi-ucum.example/v1", "openai") == "custom"

    def test_empty_endpoint_is_custom(self):
        assert preset_for(None, "openai") == "custom"


class TestDefaultPort:
    """Port tek yerde tanimli olmali."""

    def test_value(self):
        assert DEFAULT_PORT == 8791

    def test_no_stray_literals(self):
        """CLI, sunucu ve betikler ayni sayiyi ayri ayri tasimamali.

        Uc yerde ayri ayri yazildiginda biri degisip digerleri kalmisti ve
        betikle CLI farkli portlara baglaniyordu.
        """
        for name in ("cli.py", "web/app.py"):
            source = (STATIC_DIR.parent.parent / name).read_text(encoding="utf-8")
            assert "DEFAULT_PORT" in source, name
            assert not re.search(r"port[^=\n]*=\s*8\d{3}\b", source), name


class TestSettingsForm:
    """Ayarlar ekrani bir DUZENLEYICI; arka plan onu ezmemeli."""

    def test_polling_does_not_rewrite_the_form(self):
        """Kaydedilmemis secim on iki saniye sonra geri alinmamali.

        `loadOverview` her yoklamada `renderSettings()` cagiriyordu ve o da
        butun alanlari sunucudaki degerlerle dolduruyordu: saglayiciyi Groq
        yapip beklerseniz sessizce vLLM'e donuyordu.
        """
        js = _asset("app.js")
        block = js[js.index("async function loadOverview"):]
        block = block[:block.index("\nfunction ")]
        assert "refreshSettingsStatus()" in block
        assert "renderSettings()" not in block, "yoklama formu yeniden dolduruyor"

    def test_status_refresh_touches_no_input(self):
        """Durum tazelemesi yalnizca okunur metinleri guncellemeli."""
        js = _asset("app.js")
        start = js.index("function refreshSettingsStatus()")
        block = js[start:js.index("\nfunction ", start + 1)]
        assert ".value =" not in block, "durum tazelemesi bir alani yaziyor"
        assert ".checked =" not in block

    def test_model_fields_offer_the_live_list(self):
        html = _asset("index.html")
        for field in ("set-model-lead", "set-model-worker", "set-model-fast"):
            pattern = rf'id="{field}"[^>]*list="model-options"'
            assert re.search(pattern, html), field
        assert 'id="model-options"' in html


class TestDocumentList:
    """Geliştirme sekmesindeki belge listesi."""

    @staticmethod
    def _rule(selector: str) -> str:
        css = _asset("styles.css")
        start = css.index(selector + " {")
        return css[start:css.index("}", start)]

    def test_scrolls_instead_of_growing(self):
        """Yirmi yedi belge paneli buyutup yanindaki kosu panelini itiyordu."""
        rule = self._rule(".doc-chips")
        assert "overflow-y: auto" in rule
        assert "max-height" in rule

    def test_rows_start_at_the_same_left_edge(self):
        """Tur rozeti sabit genislikte olmasa adlar kayarak basliyor."""
        rule = self._rule(".doc-chips li .badge")
        assert "min-width" in rule
        row = self._rule(".doc-chips li")
        assert "justify-content: flex-start" in row
        assert "text-align: left" in row


class TestProviderRoutes:
    @pytest.fixture
    def client(self, settings: Settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as test_client:
            yield test_client

    def test_catalog_endpoint(self, client):
        data = client.get("/api/providers").json()
        assert len(data["providers"]) >= 15
        assert data["current"] in {p["key"] for p in data["providers"]}

    def test_catalog_reports_who_has_no_listing(self, client):
        """Model listesi sunmayan saglayici onceden soylenmeli.

        Yoksa "Modelleri getir" bos donunce kullanici bir sey bozuldu sanir.
        """
        assert "perplexity" in client.get("/api/providers").json()["no_listing"]

    def test_model_listing_reports_a_missing_endpoint(self, client, settings):
        settings.openai_base_url = None
        result = client.post("/api/settings/models", json={}).json()
        assert result["ok"] is False
        assert "tanimli degil" in result["error"]
