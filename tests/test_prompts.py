"""Ajan yonergeleri: dil secimi ve ceviri butunlugu.

Yonergeler modele giden metindir; ajan `{language}` yer tutucusu sayesinde
zaten secilen dilde YAZIYORDU ama TALIMATLARI Turkce okuyordu. Ingilizce bir
kurulumda kullanicinin ozellestirmek icin actigi dosya da Turkce'ydi.
"""

from __future__ import annotations

import pytest

from deerx.agents.prompts import PACKAGE_PROMPTS, ROLES, compose_system, load_prompt

EN_DIR = PACKAGE_PROMPTS / "en"


def _tr_files() -> list[str]:
    return sorted(p.stem for p in PACKAGE_PROMPTS.glob("*.md"))


class TestLanguageSelection:
    def test_turkish_is_the_default(self, settings):
        settings.language = "tr"
        assert "Sen DeerX'sin" in load_prompt("_shared", settings)

    def test_english_is_used_when_selected(self, settings):
        settings.language = "en"
        assert "You are DeerX" in load_prompt("_shared", settings)

    def test_a_missing_translation_falls_back_instead_of_crashing(self, settings):
        """Kismi ceviri calisir durumda kalmali.

        Bir rolun Ingilizcesi yoksa o rol Turkce yonergeyle calisir,
        digerleri Ingilizce -- hicbir sey cokmez.
        """
        settings.language = "en"
        for ad in _tr_files():
            metin = load_prompt(ad, settings)
            assert metin.strip(), ad

    def test_a_workspace_override_still_wins(self, settings, tmp_path):
        """Kullanicinin kendi yonergesi her dilin onunde gelir."""
        settings.language = "en"
        settings.prompts_dir.mkdir(parents=True, exist_ok=True)
        (settings.prompts_dir / "_shared.md").write_text("BENIM", encoding="utf-8")
        assert load_prompt("_shared", settings) == "BENIM"

    @pytest.mark.parametrize("role", sorted(ROLES))
    def test_every_role_composes_in_both_languages(self, settings, role):
        for dil in ("tr", "en"):
            settings.language = dil
            metin = compose_system(role, settings)
            assert len(metin) > 400, f"{role}/{dil} cok kisa"
            assert "{workspace}" not in metin, "yer tutucu doldurulmamis"


class TestTranslationCoverage:
    def test_the_shared_preamble_is_translated(self):
        """Her rol bunu okuyor; eksikse butun kurulum yarim Turkce olur."""
        assert (EN_DIR / "_shared.md").is_file()

    def test_translated_files_have_no_turkish_letters(self):
        """Ingilizce dosyada Turkce harf, yarim kalmis ceviri isaretidir."""
        turkce = set("ıİşŞğĞçÇöÖüÜ")
        for path in EN_DIR.glob("*.md"):
            metin = path.read_text(encoding="utf-8")
            # Turkce ornek metinler tirnak icinde gecebilir; yalnizca govdeye bak.
            govde = "\n".join(
                satir for satir in metin.splitlines() if not satir.strip().startswith(">")
            )
            kalan = turkce & set(govde)
            assert not kalan, f"{path.name}: {sorted(kalan)}"

    def test_placeholders_survive_translation(self):
        """`{workspace}` gibi yer tutucular kaybolursa prompt bozulur."""
        for path in EN_DIR.glob("*.md"):
            tr_path = PACKAGE_PROMPTS / path.name
            if not tr_path.is_file():
                continue
            import re

            tr_ph = set(re.findall(r"\{(\w+)\}", tr_path.read_text(encoding="utf-8")))
            en_ph = set(re.findall(r"\{(\w+)\}", path.read_text(encoding="utf-8")))
            assert tr_ph == en_ph, f"{path.name}: {tr_ph ^ en_ph}"

    def test_qa_prompt_asks_for_attacks_the_spec_never_names(self):
        """QA istemi sartname disini de denemeli, iki dilde de.

        OLCULDU. Uctan uca bir kosuda uretilen urunde CRLF baslik
        enjeksiyonu vardi: saklanan bir URL'deki `\\r\\n` yonlendirmede
        saldirganin satirini gercek bir yanit basligi yapiyordu. QA fazi
        titiz calismisti -- XSS'i ayrica test etmis, ekran goruntusu
        birakmis, gercek bir hata bulup duzeltmisti. Ama 61 bosluk
        kaydinin hicbirinde CRLF, kontrol karakteri ya da baslik
        enjeksiyonu gecmiyordu.

        Sebep: guvenlik dusuncesi SARTNAMEYE bagliydi. Sartname
        `javascript:` semasini XSS deligi diye adlandirmisti, orada titiz
        davranildi; response splitting'den soz etmiyordu, oraya kimse
        bakmadi. Bolum kalkarsa o kor nokta geri gelir.
        """
        for yol in (PACKAGE_PROMPTS / "qa.md", EN_DIR / "qa.md"):
            metin = yol.read_text(encoding="utf-8")
            assert "\\r\\n" in metin, (
                f"{yol.name}: baslik enjeksiyonu denemesi anlatilmali"
            )
            assert "record_gaps" in metin
            # Saldiri, sartnamenin tehdit listesinden degil urunun ne
            # yaptigindan turetilmeli; bunu soyleyen cumle sart.
            assert ("ürünün ne yaptığından" in metin
                    or "what the product does" in metin), (
                f"{yol.name}: saldirinin nereden turetilecegi yazmali"
            )

    def test_artifact_names_are_not_translated(self):
        """Cikti dosya adlari sozlesmedir; cevrilirse faz sozlesmesi kirilir.

        `PHASE_DELIVERABLE` bu adlari bekliyor; Ingilizce yonerge
        `gap-analysis.md` yazdirirsa faz "cikti uretilmedi" der.
        """
        from deerx.pipeline.orchestrator import PHASE_DELIVERABLE

        beklenen = {desen for desen, _ in PHASE_DELIVERABLE.values()}
        birlesik = "\n".join(
            p.read_text(encoding="utf-8") for p in EN_DIR.glob("*.md")
        )
        for desen in beklenen:
            if "*" in desen:
                continue
            kaynak = PACKAGE_PROMPTS / f"{_role_for(desen)}.md"
            if not (EN_DIR / kaynak.name).is_file():
                continue
            assert desen in birlesik, f"{desen} Ingilizce yonergelerde gecmiyor"


def _role_for(pattern: str) -> str:
    return {
        "analiz-raporu.md": "analyst",
        "arastirma-notlari.md": "researcher",
        "bosluk-analizi.md": "assessor",
        "mimari.md": "architect",
        "gelistirme-plani.md": "planner",
        "qa-raporu.md": "qa",
        "dogrulama-raporu.md": "reviewer",
        "staging-raporu.md": "staging",
        "canli-cikis-raporu.md": "live",
    }.get(pattern, "_shared")
