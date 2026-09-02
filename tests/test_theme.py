"""Tema ve dugme renkleri.

Butun olcumler styles.css'teki degerlerden hesaplanir. Goz karariyla secilen
bir ton kolayca esigin altina duser ve kimse fark etmez -- bu dosyanin
varlik sebebi, "guzel gorunuyor" ile "okunuyor" arasindaki farki olcmek.

Iki ayri esik var:
  * metin/zemin  -> WCAG AA 4.5:1
  * arayuz bileseni siniri (WCAG 1.4.11) -> 3:1
"""

from __future__ import annotations

import re

import pytest

from deerx.web.app import STATIC_DIR
from tests.test_web import TestPalette

THEMES = ("light", "dark")


def _css() -> str:
    return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def themes() -> dict[str, dict[str, str]]:
    return TestPalette._themes()


def _ratio(a: str, b: str) -> float:
    return TestPalette._contrast(a, b)


class TestControlTokens:
    """Dolu kontroller icin ayri jeton ailesi."""

    def test_tokens_exist_in_both_themes(self, themes):
        needed = {
            "accent-solid", "accent-solid-hover", "accent-solid-text",
            "err-solid", "err-solid-hover", "err-solid-text",
            "control-border", "control-border-hover",
        }
        for theme in THEMES:
            missing = needed - set(themes[theme])
            assert not missing, f"{theme}: eksik jeton {sorted(missing)}"

    def test_filled_buttons_are_dark_blue_on_white(self, themes):
        """Birincil dugme her iki temada da beyaz yazili olmali.

        `--accent` hem isaretci hem dolgu olarak kullanilirken karanlik
        temada dugme acik mavi zemine koyu yazi oluyordu: dugmeden cok
        rozete benziyordu.
        """
        for theme in THEMES:
            t = themes[theme]
            assert t["accent-solid-text"].lower() == "#ffffff", theme
            assert t["err-solid-text"].lower() == "#ffffff", theme

    @pytest.mark.parametrize("fill,ink", [
        ("accent-solid", "accent-solid-text"),
        ("accent-solid-hover", "accent-solid-text"),
        ("err-solid", "err-solid-text"),
        ("err-solid-hover", "err-solid-text"),
    ])
    def test_button_text_is_readable(self, themes, fill, ink):
        for theme in THEMES:
            r = _ratio(themes[theme][fill], themes[theme][ink])
            assert r >= 4.5, f"{theme}: {ink} / {fill} = {r:.2f}"

    @pytest.mark.parametrize("fill", ["accent-solid", "err-solid"])
    def test_button_fill_separates_from_the_panel(self, themes, fill):
        """Dolu dugme oturdugu yuzeyden ayrisabilmeli (1.4.11: 3:1)."""
        for theme in THEMES:
            r = _ratio(themes[theme][fill], themes[theme]["surface"])
            assert r >= 3.0, f"{theme}: {fill} / surface = {r:.2f}"

    # Kenarlik hangi zeminin uzerinde duruyorsa onunla olculur. Dinlenme
    # halinde dugmenin zemini `surface-2`, panel `surface`; hover'da zemin
    # `surface-3`'e ve kenarlik da hover rengine geciyor -- ciftler bu yuzden
    # boyle eslenir, `control-border`'i hover zeminiyle olcmek gercekte hic
    # olusmayan bir kombinasyonu sinardi.
    @pytest.mark.parametrize("token,surface", [
        ("control-border", "surface"),
        ("control-border", "surface-2"),
        ("control-border-hover", "surface-3"),
    ])
    def test_control_border_meets_the_component_threshold(self, themes, token, surface):
        """Ikincil dugmeyi yalnizca kenarligi tanimlar; gorunur olmali.

        Zemini panelden 1.09:1 farkediyor, yani sinir gorunmezse kontrol
        de gorunmez. Eski `--border-strong` isikta 1.6:1, karanlikta
        1.93:1 idi -- ikisi de 3:1'in altinda.
        """
        for theme in THEMES:
            r = _ratio(themes[theme][token], themes[theme][surface])
            assert r >= 3.0, f"{theme}: {token} / {surface} = {r:.2f}"

    def test_hover_moves_in_the_right_direction(self, themes):
        """Isikta hover koyulasir, karanlikta acilir -- basildigi hissedilsin."""
        def lum(color: str) -> float:
            return TestPalette._contrast(color, "#000000")

        assert lum(themes["light"]["accent-solid-hover"]) < lum(themes["light"]["accent-solid"])
        assert lum(themes["dark"]["accent-solid-hover"]) > lum(themes["dark"]["accent-solid"])


class TestButtonRules:
    """Kurallarin dogru jetonlari kullanmasi."""

    def _rule(self, selector: str) -> str:
        css = _css()
        start = css.index(selector + " {")
        return css[start:css.index("}", start)]

    def test_primary_uses_the_solid_token(self):
        rule = self._rule(".btn-primary")
        assert "var(--accent-solid)" in rule
        assert "var(--accent-solid-text)" in rule
        assert "var(--accent)" not in rule.replace("var(--accent-solid)", "")

    def test_danger_is_filled_not_outlined(self):
        """Bir kosuyu durduran dugme, yanindaki 'Baslat'tan silik olamaz."""
        rule = self._rule(".btn-danger")
        assert "background: var(--err-solid)" in rule
        assert "transparent" not in rule

    def test_controls_use_the_control_border(self):
        """Dugme, girdi ve cip dekoratif kenarligi kullanmamali."""
        css = _css()
        for selector in (".btn {", ".chip {", ".queue-dot {"):
            block = css[css.index(selector):]
            block = block[:block.index("}")]
            assert "var(--control-border)" in block, selector
            assert "var(--border-strong)" not in block, selector

    def test_no_hardcoded_ink_on_accent_fills(self):
        """`background: var(--accent)` uzerine sabit `#fff` yazilmamali.

        Soru kuyrugundaki etkin nokta boyleydi: karanlik temada acik mavi
        zemine beyaz yazi, 2.62:1 -- AA'nin cok altinda. Metin rengi
        zeminle birlikte temaya gore degismeli.
        """
        css = _css()
        for match in re.finditer(r"\{[^}]*background:\s*var\(--accent\)[^}]*\}", css):
            body = match.group(0)
            assert not re.search(r"color:\s*#(fff|ffffff)\b", body, re.I), body

    def test_buttons_acknowledge_the_press(self):
        assert ".btn:active:not(:disabled)" in _css()


class TestSemanticFills:
    """Anlamsal rengi dolgu olarak kullanan her yer temaya uymali.

    Kalip su: `background: var(--err)` gibi bir dolgunun uzerine
    `color: var(--surface)` yazilir -- yuzey rengi temayla birlikte dondugu
    icin murekkep de doner. Sabit bir `#fff` yazildiginda kalip kirilir:
    isik temasinda dogru gorunur, karanlik temada acik kirmizinin uzerinde
    2.35:1'de kalir ve kimse fark etmez.
    """

    FILLS = ["ok", "warn", "err", "info"]

    def test_no_hardcoded_ink_on_semantic_fills(self):
        css = _css()
        for match in re.finditer(r"\{[^{}]*\}", css):
            body = match.group(0)
            fill = re.search(r"background(?:-color)?:\s*var\(--(ok|warn|err|info|accent)\)", body)
            if not fill:
                continue
            ink = re.search(r"(?<!-)color:\s*(#[0-9a-fA-F]{3,8})", body)
            assert ink is None, f"--{fill.group(1)} dolgusuna sabit murekkep: {body.strip()}"

    @pytest.mark.parametrize("fill", FILLS)
    def test_surface_ink_reads_on_every_fill(self, themes, fill):
        """`--surface` murekkebi her anlamsal dolgunun uzerinde okunmali."""
        for theme in THEMES:
            r = _ratio(themes[theme]["surface"], themes[theme][fill])
            assert r >= 4.5, f"{theme}: surface / {fill} = {r:.2f}"

    def test_only_the_mockup_frame_is_hardcoded_white(self):
        """Tek istisna belgelenmis olmali.

        Mockup cercevesindeki belge ajanin urettigi bagimsiz bir HTML
        sayfasi; bizim temamizi bilmiyor ve koyu yaziyla geliyor. Koyu
        zemin verirsek mockup okunmaz olur -- bu yuzden beyaz kalir.
        """
        css = _css()
        # Palet bloklarini disla
        palette = [m.span() for m in re.finditer(r":root[^{]*\{[^}]*\}", css)
                   if "--bg:" in m.group(0)]
        stray = []
        for m in re.finditer(r"#(?:fff|ffffff)\b", css, re.I):
            if any(a <= m.start() < b for a, b in palette):
                continue
            line_start = css.rfind("\n", 0, m.start()) + 1
            block_start = css.rfind("{", 0, m.start())
            selector = css[css.rfind("}", 0, block_start) + 1:block_start].strip()
            stray.append((selector, css[line_start:css.find("\n", m.start())].strip()))
        assert len(stray) == 1 and "mockup-frame" in stray[0][0], stray


class TestControlAffordance:
    """Kontrolun kontrol oldugu anlasilmali."""

    def test_standalone_actions_are_not_ghost(self):
        """Govdede tek basina duran eylem hayalet olamaz.

        Hayalet dugme cevresinden anlam alir: panel basligi, arac cubugu,
        tablo satiri. `.run-actions` govdenin icinde durur; oradaki bir
        hayalet dugme duz yazidan ayirt edilemez -- "Baglantiyi test et"
        boyleydi.
        """
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for match in re.finditer(r'<div class="run-actions">(.*?)</div>', html, re.S):
            block = match.group(1)
            assert "btn-ghost" not in block, block.strip()[:160]

    def test_active_nav_has_a_marker_beyond_background(self):
        """Etkin bolum isareti zemine bagli kalmamali.

        `--accent-soft` ray zemininden 1.14:1 farkediyor: hap sekli
        gorunmuyor, geriye yalnizca yazi rengi kaliyordu.
        """
        css = _css()
        block = css[css.index(".rail-item.is-active {"):]
        block = block[:block.index("}")]
        assert "box-shadow: inset" in block, block

    def test_icon_only_button_has_a_visible_boundary(self):
        """Yaninda etiket yoksa siniri "dugme" demek zorunda."""
        css = _css()
        block = css[css.index(".artifact-close {"):]
        block = block[:block.index("}")]
        assert "var(--control-border)" in block

    def test_disabled_buttons_stay_legible(self, themes):
        """Devre disi etiket okunabilmeli.

        `opacity: .45` dolguyu ve yaziyi birlikte solduruyordu: isik
        temasinda devre disi birincil dugme 1.48:1'e dusuyordu. "Baslat"
        bir kosu surerken hep bu haldedir.
        """
        css = _css()
        block = css[css.index(".btn:disabled {"):]
        block = block[:block.index("}")]
        assert "opacity" not in block, "saydamlik dolgu ile yaziyi birlikte soldurur"
        for theme in THEMES:
            r = _ratio(themes[theme]["text-3"], themes[theme]["surface-2"])
            assert r >= 4.5, f"{theme}: devre disi etiket {r:.2f}"


class TestListIndent:
    """Isaretcisiz listeler kutularinin sol kenarindan baslamali.

    Tarayici her `ul` ve `ol` icin 40 piksellik bir `padding-inline-start`
    verir. Sifirlama yalnizca `* { margin: 0 }` idi, yani bu dolgu yerinde
    kaliyordu; `list-style: none` isaretciyi kaldirir ama dolguya dokunmaz.
    Sonuc: arayuzdeki isaretcisiz her liste sessizce 40 piksel iceri
    kaymisti -- adim listesinde ic ice iki liste oldugu icin kutucuklar
    kendi kutularinin kenarindan 93 piksel uzaktaydi.

    Goz kararinda fark edilmesi zor, cunku her liste ayni miktarda kayiyor
    ve dengeli gorunuyor; ancak kutunun kenarligiyla karsilastirinca
    ortaya cikiyor.
    """

    @staticmethod
    def _css() -> str:
        return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    def test_default_list_padding_is_reset(self):
        assert re.search(r"^\s*ul,\s*ol\s*\{[^}]*padding-left:\s*0", self._css(), re.M), (
            "ul/ol varsayilan dolgusu sifirlanmamis; isaretcisiz listeler "
            "40 piksel iceri kayar."
        )

    def test_prose_lists_keep_room_for_markers(self):
        """Sifirlama metin icerigini vurmamali: madde isaretleri gorunmeli."""
        match = re.search(r"\.prose ul,\s*\.prose ol\s*\{([^}]*)\}", self._css())
        assert match, ".prose listeleri icin dolgu tanimi yok"
        assert "padding-left" in match.group(1)
        assert "padding-left: 0" not in match.group(1)

    @pytest.mark.parametrize(
        "selector",
        [".step-list", ".doc-chips", ".wf-steps"],
    )
    def test_ui_lists_do_not_reintroduce_the_indent(self, selector: str):
        """Bir kural kendi dolgusunu geri koyarsa sifirlama bosa gider."""
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self._css())
        assert match, f"{selector} icin kural yok"
        body = match.group(1)
        for prop in ("padding-left", "padding-inline-start"):
            assert prop not in body, f"{selector} dolguyu geri koyuyor"
        # Kisa yazim da girinti getirebilir: `padding: 8px 12px` gibi.
        kisa = re.search(r"(?<!-)padding:\s*([^;]+);", body)
        if kisa:
            parcalar = kisa.group(1).split()
            sol = parcalar[3] if len(parcalar) == 4 else (
                parcalar[1] if len(parcalar) >= 2 else parcalar[0])
            assert sol.startswith("0"), f"{selector} sol dolgu veriyor: {sol}"
