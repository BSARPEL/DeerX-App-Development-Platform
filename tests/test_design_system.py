"""Tasarim sisteminin disiplinini civileyen testler.

Bu dosya bir yeniden tasarimdan dogdu. Sikayet "cok amatorce" idi ve
olculdu: on uc notr jetonun hepsi mavi tonluydu, olay akisi 12px
Cascadia ile ajanin cumlelerini basiyordu (genel bakista metin
elemanlarinin %64'u mono), 91 kural renkli metin yaziyordu, on farkli
kontrol yuksekligi, on yedi hap sinifi, on bir ayri opacity degeri
vardi. Jeton katmani disiplinliydi; amatorluk jetonlarin USTUNE kurulan
bilesen katmanindaydi.

Buradaki testler o disiplini uc ay sonraya tasir: bir sonraki el "guzel
gorunuyor" diye 1.55 satir yuksekligi ya da mono bir sayac ekledigi
anda dusmeli.

`TestPalette` (test_web.py) ve `tests/test_theme.py` renk ve kontrol
jetonlarini kilitler; bu dosya ONLARIN ustundeki katmani kilitler.
"""

from __future__ import annotations

import math
import re

import pytest

from deerx.web.app import STATIC_DIR
from tests.test_web import TestPalette


def _css() -> str:
    return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


def _govde() -> str:
    """`body {` sonrasi: jeton bloklari disarida kalir."""
    css = _css()
    return css[css.index("body {"):]


def _rule(selector: str) -> str:
    css = _css()
    start = css.index(selector + " {")
    return css[start:css.index("}", start)]


def _blocks(css: str):
    """(secici, govde) ciftleri; yorumlar ayiklanmis."""
    temiz = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", temiz)


@pytest.fixture(scope="module")
def themes():
    return TestPalette._themes()


# --------------------------------------------------------------------- #
# Tipografi jetonlari
# --------------------------------------------------------------------- #
class TestLineHeightComesFromTokens:
    """Satir yuksekligi UC jeton; baska deger yok.

    OLCULDU: jeton varken 24 satirda 10 farkli ham deger yaziliyordu
    (1.35, 1.45, 1.5, 1.55, 1.6, 1.65, 1.68...). Bunlar gozle ayirt
    edilmez ama toplaninca hizalari kaydirir: iki sutunlu panelde
    paragraf tabanlari tutmaz. Tek istisna `1` -- ikon ve glif kutulari.
    """

    def test_no_raw_line_height_in_the_body(self):
        ham = re.findall(r"line-height:\s*([0-9.]+);", _govde())
        sapan = sorted({v for v in ham if v != "1"})
        assert not sapan, "jeton disi satir yuksekligi: " + repr(sapan)

    def test_the_three_tokens_are_used(self):
        govde = _govde()
        for jeton in ("--lh-tight", "--lh-snug", "--lh-text"):
            assert f"var({jeton})" in govde, jeton + " hic kullanilmiyor"


class TestLetterSpacingComesFromTokens:
    """Sikilastirma yalnizca >= 24px'te ve yalnizca jetonla.

    15-19px'te negatif tracking Segoe UI'da harfleri birbirine
    yapistirir ("DeerX" markasinda r-X sikisiyordu).
    """

    def test_no_raw_letter_spacing(self):
        ham = re.findall(r"letter-spacing:\s*(-?[0-9.]+em);", _govde())
        assert not ham, "jeton disi harf araligi: " + repr(sorted(set(ham)))


class TestControlHeightsComeFromTokens:
    """On farkli kontrol yuksekligi (42/40/34/32/29/28/27/26/22/20) iki
    jetona indi: `--ctl-h` 36 ve `--ctl-h-sm` 28."""

    @pytest.mark.parametrize("selector,token", [
        (".btn", "--ctl-h"),
        (".btn-sm", "--ctl-h-sm"),
        (".chip", "--ctl-h-sm"),
        (".tab", "--ctl-h"),
        (".pager-btn, .pager-num", "--ctl-h-sm"),
        (".icon-btn", "--ctl-h-sm"),
        (".artifact-close", "--ctl-h-sm"),
    ])
    def test_control_uses_the_height_token(self, selector, token):
        assert f"var({token})" in _rule(selector), f"{selector} {token} kullanmiyor"

    def test_inputs_share_the_control_height(self):
        css = _css()
        bas = css.index('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]),\nselect, textarea {')
        blok = css[bas:css.index("}", bas)]
        assert "var(--ctl-h)" in blok


# --------------------------------------------------------------------- #
# Renk disiplini
# --------------------------------------------------------------------- #
def _lch_chroma(hex_color: str) -> float:
    """CIE L*C*h -- C* bileseni. Notr bir rengin 'ne kadar renkli'
    oldugunu olcer; hue'dan bagimsiz."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.0
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    a = 500 * (fx - fy)
    bb = 200 * (fy - fz)
    return math.hypot(a, bb)


def _hue(hex_color: str) -> float:
    """CIE L*C*h -- h bileseni (derece). Bir rengin markanin hue'sunda
    kalip kalmadigini olcer."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.0
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return math.degrees(math.atan2(200 * (fy - fz), 500 * (fx - fy))) % 360


def _lstar(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    y = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 116 * (y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116) - 16


class TestNeutralsAreGrey:
    """ICERIK notrleri gri: kroma C* <= 5.

    OLCULDU: on uc notr jetonun hepsi LCh hue 265-272'de ve kroma acikta
    C* 4-13, koyuda 6-21 idi -- ekran "mavi" ile bitiyordu ve marka
    mavisi, mavi grilerin arasinda bir mavi daha olarak kaliyordu.
    Kroma dusuruldu, L* korundu: hicbir kontrast orani degismedi.

    TEZGAH BU LISTEDE DEGIL, ve olmamasi bir gevsetme degil kuralin
    yarisi. Sorun "her yerde biraz mavi" idi; cozumu "hicbir yerde mavi"
    degil, mavinin TEK bir yere toplanmasi. Tezgah (ray + ust bar) veri
    tasimaz; markanin durdugu duzlem orasi ve `--chrome` artik logonun
    kendi laciverti (#0d2d55, C* 28). Kimlik ekranin kenarinda, veri
    notr kagitta. Tezgahin gercekten marka OLDUGUNU asagidaki
    `TestTheChromeCarriesTheBrand` olcer -- yani buradan cikarilmasi
    onu denetimsiz birakmiyor, BASKA bir denetime bagliyor.
    """

    NEUTRALS = ("bg", "surface", "surface-raised",
                "surface-2", "surface-3", "border", "border-strong",
                "control-border", "control-border-hover",
                "text", "text-2", "text-3")

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_neutral_chroma_stays_low(self, themes, theme):
        renkli = []
        for ad in self.NEUTRALS:
            c = _lch_chroma(themes[theme][ad])
            if c > 5.0:
                renkli.append(f"{ad} C*={c:.1f}")
        assert not renkli, f"{theme}: notr jeton renkli: " + ", ".join(renkli)


class TestTheChromeCarriesTheBrand:
    """Tezgah markanin duzlemi: notr bir griye geri DUSMEMELI.

    `--chrome` logonun baskin pikselinden gelir: #0d2d55, LCh hue 280.
    Acikta jeton logonun hex'inin kendisidir; koyuda ayni hue daha
    dusuk L*'ta (L* 6.5) yasar ve kroma fiziken dusmek zorundadir --
    bu yuzden esik iki temada ayri.

    Bu test olmadan `TestNeutralsAreGrey`den cikarilan iki jeton
    denetimsiz kalir ve bir sonraki el onlari sessizce griye
    cevirebilirdi; o da tam olarak bu yeniden tasarimin geri aldigi sey.
    """

    ESIK = {"light": 20.0, "dark": 10.0}

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_the_chrome_is_chromatic(self, themes, theme):
        c = _lch_chroma(themes[theme]["chrome"])
        assert c >= self.ESIK[theme], f"{theme}: tezgah grilesti, C*={c:.1f}"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_the_chrome_keeps_the_brand_hue(self, themes, theme):
        """Marka hue'su 280; sapma +/- 15 derece."""
        h = _hue(themes[theme]["chrome"])
        fark = min(abs(h - 280.1), 360 - abs(h - 280.1))
        assert fark <= 15.0, f"{theme}: tezgah hue {h:.0f}, markadan {fark:.0f} uzak"

    def test_the_light_chrome_is_the_logo_itself(self, themes):
        """Acikta jeton logonun olculen hex'i: turev degil, kendisi."""
        assert themes["light"]["chrome"].lower() == "#0d2d55"


class TestTheChromeRecedesFromThePaper:
    """Tezgah (ray + ust bar) kagittan geri cekilmeli.

    Koyuda duzlem farki WCAG oraniyla olculmez (formul siyaha yakin
    sikisir); olcut CIE L* farki >= 4.5. Acikta da ayni olcut: ray
    hover/etkin dolgusunun gorunmesi bu farka dayaniyor.
    """

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_chrome_and_paper_are_different_planes(self, themes, theme):
        t = themes[theme]
        fark = abs(_lstar(t["chrome"]) - _lstar(t["bg"]))
        assert fark >= 4.5, f"{theme}: tezgah/kagit dL* {fark:.1f}"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_the_hover_step_exists_on_the_chrome(self, themes, theme):
        t = themes[theme]
        assert "chrome-hover" in t, "tezgah hover jetonu yok"
        fark = abs(_lstar(t["chrome-hover"]) - _lstar(t["chrome"]))
        assert fark >= 3.0, f"{theme}: tezgah/hover dL* {fark:.1f}"


class TestThePanelRisesOffTheGround:
    """Panel zeminden AYRI bir duzlem. Duzlugun kaynagi buydu.

    OLCULDU: `--surface` ile `--bg` iki temada da birebir ayni hex'ti
    (acikta #fafbfc, koyuda #17191c) ve bu bilincli bir karar olarak
    yazilmisti -- "panel kagittan yukselmez". Sonucu ekranda tek bir
    duzlem olmasiydi: bir panelin sinirini yalnizca tek bir sac teli
    cizgi tasiyor, panel basligi ile sayfa basligi ayni kagitta
    yariyordu. Sahibi "cok duz duruyor" dedi; olcum onu dogruladi.

    Esik dL* >= 3.5: bundan azi 1440px'te bir ekranda gozle ayirt
    edilmiyor ve kural pratikte yeniden kaybolur.
    """

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_the_panel_and_the_ground_are_different_planes(self, themes, theme):
        t = themes[theme]
        assert t["surface"].lower() != t["bg"].lower(), f"{theme}: panel = zemin"
        fark = abs(_lstar(t["surface"]) - _lstar(t["bg"]))
        assert fark >= 3.5, f"{theme}: panel/zemin dL* {fark:.1f}"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_the_well_steps_off_the_panel(self, themes, theme):
        """Kuyu (thead, kod, satir hover) panelden ayrilmali."""
        t = themes[theme]
        fark = abs(_lstar(t["surface-2"]) - _lstar(t["surface"]))
        assert fark >= 3.0, f"{theme}: panel/kuyu dL* {fark:.1f}"

    def test_the_panel_declares_its_own_surface(self):
        """`.panel` artik zemine yapisik bir bolum degil, bir yuzey."""
        rule = _rule(".panel")
        assert "var(--surface)" in rule, ".panel yuzeysiz"
        assert "var(--border)" in rule, ".panel sinirsiz"


class TestTextReadsOnEverySurface:
    """TestPalette'in PAIRS listesine dokunmadan alti cift daha.

    Yukselen yuzey (`--surface-raised`) ve TEZGAH uzerinde de metin AA
    olmali; once bu ciftler hic olculmuyordu.

    Tezgah ciftleri artik `--chrome-text*` ile olculuyor, `--text*` ile
    degil -- cunku tezgah lacivert bir duzlem ve icerigin murekkebi
    orada okunmaz (olculdu: `--text-2` / `--chrome` = 1.67). Bu bir
    esnetme degil: aradaki cift SILINMEDI, dogru jetonla kuruldu ve
    sayisi ayni kaldi. Tezgahta `--text*` kullanan bir kural kalirsa
    `test_the_chrome_uses_its_own_ink` duser.
    """

    PAIRS = [("chrome-text", "chrome", 4.5), ("chrome-text-2", "chrome", 4.5),
             ("text-2", "surface-raised", 4.5), ("text-3", "surface-raised", 4.5),
             ("chrome-text", "chrome-hover", 4.5), ("chrome-text-2", "chrome-hover", 4.5)]

    def test_the_chrome_uses_its_own_ink(self):
        """Tezgahtaki hicbir kural icerik murekkebini kullanmaz.

        Iki istisna, ikisi de zemini KAGIT olan denetimler: etkin ray
        maddesi ve secili dil. Onlarin zemini `--surface`, murekkebi de
        `--text` olmali -- kuralin kendisi degil, tersi.
        """
        kacak = []
        for secici, govde in _blocks(_css()):
            s = secici.strip()
            if not (s.startswith(".rail") or s.startswith(".topbar")
                    or s.startswith(".lang-") or s.startswith(".run-pill")
                    or s.startswith(".run-dot") or s.startswith(".brand")
                    or s.startswith(".stat-inline") or s.startswith(".project-switch")):
                continue
            if "is-active" in s or 'aria-pressed="true"' in s or "option" in s:
                continue
            if re.search(r"color:\s*var\(--text(-[23])?\)", govde):
                kacak.append(s)
        assert not kacak, "tezgahta icerik murekkebi: " + "; ".join(kacak)

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_pairs(self, themes, theme):
        zayif = []
        for fg, bg, need in self.PAIRS:
            r = TestPalette._contrast(themes[theme][fg], themes[theme][bg])
            if r < need:
                zayif.append(f"{fg}/{bg} = {r:.2f}")
        assert not zayif, f"{theme}: " + "; ".join(zayif)


class TestDarkBlocksStayInSync:
    """Iki koyu blok (medya sorgusu ve `data-theme`) BIREBIR ayni olmali.

    `_themes` ilk goreni alir: biri guncellenip oteki unutulursa OS
    tercihiyle acilan ekran ile dugmeyle acilan ekran farkli gorunur ve
    palet testi bunu yakalamaz.
    """

    def test_both_dark_blocks_carry_the_same_tokens(self):
        css = _css()
        bloklar = [m.group(2) for m in re.finditer(r"(:root[^{]*)\{([^}]*)\}", css)
                   if "--bg:" in m.group(2)]
        assert len(bloklar) == 3, len(bloklar)
        koyu = [dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", b)) for b in bloklar[1:]]
        assert koyu[0] == koyu[1], "iki koyu blok ayrilmis"

    def test_the_semantic_colours_weigh_the_same_as_the_accent(self, themes):
        """Koyuda vurgu bes kromatik rengin en solgunuydu (L* 65.8 <
        69-78) ve marka durum renklerine yeniliyordu."""
        t = themes["dark"]
        vurgu = _lstar(t["accent"])
        for ad in ("ok", "warn", "err", "info"):
            fark = abs(_lstar(t[ad]) - vurgu)
            assert fark <= 8.0, f"koyu: {ad} L* vurgudan {fark:.1f} uzak"


class TestColourGoesToMarksNotProse:
    """Renk metne degil ISARETE gider.

    OLCULDU: 91 kural kromatik `color:` yaziyordu -- gri metin
    kurallarinin %39'u kadar -- ve olay akisinda dokuz turun iletisi de
    renkliydi. Kirmizi metnin tek istisnasi hata CUMLESI.
    """

    def test_feed_messages_are_not_coloured_except_errors(self):
        for secici, govde in _blocks(_css()):
            if ".ev-msg" not in secici:
                continue
            m = re.search(r"color:\s*var\(--(ok|warn|accent|info)\)", govde)
            assert m is None, f"akis iletisi renkli: {secici.strip()}"

    def test_the_phase_label_carries_no_status_colour(self):
        for secici, govde in _blocks(_css()):
            if ".phase-label" not in secici or "data-status" not in secici:
                continue
            assert not re.search(r"color:\s*var\(--(ok|warn|err|accent)\)", govde), secici.strip()

    def test_chromatic_text_stays_within_budget(self):
        """Kromatik `color:` kurallari 91'den asagi cekildi; tavan 60.
        Rozet, nokta, glif ve hata cumlesi mesru; duzyazi degil."""
        # `(?<![-\w])`: `background-color`/`border-color` sayilmaz -- onlar
        # isaret, metin degil.
        temiz = re.sub(r"/\*.*?\*/", "", _govde(), flags=re.S)
        n = len(re.findall(r"(?<![-\w])color:\s*var\(--(ok|warn|err|accent|info)\)", temiz))
        assert n <= 55, f"{n} kromatik metin kurali (olculen taban 47)"


# --------------------------------------------------------------------- #
# Yuzey ve derinlik
# --------------------------------------------------------------------- #
class TestElevationIsHonest:
    """Yukselme yalnizca gercekten ustte durana; kagit ustundeki bolum
    golge tasimaz. `--surface-raised` tanimliydi ama kip, cekmece ve
    bildirim `--surface` kullaniyordu; tabloysa golge tasiyordu."""

    @pytest.mark.parametrize("selector", [".modal", ".drawer", ".toast", ".gate-card"])
    def test_floating_surfaces_use_the_raised_token(self, selector):
        assert "var(--surface-raised)" in _rule(selector), selector

    @pytest.mark.parametrize("selector", [".table-wrap", ".feed-full", ".delivery", ".questions"])
    def test_in_flow_boxes_carry_no_shadow(self, selector):
        assert "box-shadow" not in _rule(selector), selector + " golge tasiyor"

    def test_the_veil_is_a_token(self):
        """Iki elle secilmis perde rengi tek jetona indi; govdede rgba
        kalmadi."""
        assert "--veil:" in _css()
        assert "rgba(" not in _govde(), "govdede sabit rgba"

    def test_no_backdrop_blur(self):
        """Arkadaki akis bulanmaz: perde sadece koyultur."""
        assert "backdrop-filter" not in _css()

    def test_no_inset_glow_in_the_dark_theme(self):
        """`inset 0 1px 0` ic parilti 'her AI paneli' imzasiydi; derinlik
        tek modelle anlatilir: dusen golge."""
        css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
        koyu = css[css.index("@media (prefers-color-scheme: dark)"):css.index("body {")]
        assert "inset 0 1px 0" not in koyu


# --------------------------------------------------------------------- #
# Yazi tipi rolleri
# --------------------------------------------------------------------- #
class TestMonoIsReservedForIdentity:
    """Mono yalnizca degistirilemeyen dizgi icindir: kod, yol, anahtar,
    kimlik, zaman/olcu sutunu. Duzyazi, sayac ve etiket sans.

    OLCULDU: genel bakista metin elemanlarinin %64'u monoydu ve 152'si
    olay akisiydi -- ajanin duz cumleleri 12px Cascadia ile. Ust bardaki
    maliyet, ray rozeti, sekme sayaci ve kosu numarasi da monoydu:
    'programci arayuzu' dokusunun kendisi.
    """

    IZINLI = {
        "code, pre, kbd", "td.key", "td.num", ".result-src", ".task-key",
        ".task-deps", ".task-file", ".artifact-name", ".ev-time",
        ".question-key", ".package-name", ".attachment-name",
        ".wf-gate-detail", ".wf-artifact", ".run-when",
        '#view-settings input[type="password"]', ".audit-when", ".audit-from",
        ".doc-row-source", ".project-path", ".service-link", ".service-cmd",
        ".session-id",
    }

    def test_every_mono_rule_is_on_the_allowlist(self):
        sapan = []
        for secici, govde in _blocks(_css()):
            if "var(--mono)" not in govde:
                continue
            ad = " ".join(secici.split())
            if ad not in self.IZINLI:
                sapan.append(ad)
        assert not sapan, "izinsiz mono: " + repr(sapan)

    @pytest.mark.parametrize("selector", [".feed", ".rail-badge", ".tab-count",
                                          ".stat-inline-value", ".run-seq"])
    def test_prose_and_counters_are_sans(self, selector):
        assert "var(--mono)" not in _rule(selector), selector + " mono"


class TestOneBadgeGeometry:
    """Tek rozet: tonal zemin, kenarlik yok, dikdortgen.

    Once on yedi hap sinifi vardi ve `.badge` uc kanal tasiyordu (tint
    zemin + currentColor cerceve + kalin metin). Hap = etkilesimli,
    dikdortgen = statik etiket; siluet rolu tasir.
    """

    def test_the_badge_has_no_border(self):
        rule = _rule(".badge,\n.modal-badge, .questions-badge, .wf-gate-badge, .plan-bad, .wf-chip,\n#view-settings .panel-hint, .chat-change")
        assert "border: 0" in rule
        assert "currentColor" not in rule

    def test_status_badges_carry_a_non_colour_mark(self):
        """Renk koru icin hue tek kanal olamaz: durum rozetinde nokta."""
        css = _css()
        assert '.badge[data-v="done"]::before' in css
        assert '.badge[data-v="failed"]::before' in css

    def test_nominal_cells_are_not_badges(self):
        """Kategori, alan ve tur birer nominal deger; durum tasimaz."""
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        assert '<span class="cell-meta">${esc(tv("category", item.category))}' in js
        assert '<span class="cell-meta">${esc(item.area)}' in js
        assert '<span class="cell-meta">${esc(tv("kind", doc.kind))}' in js

    def test_row_status_is_a_dot_not_a_badge(self):
        """Sol serit + nokta = iki kanal; rozet ucuncuydu."""
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        assert '<span class="row-state" data-v="${esc(step.status)}">' in js
        assert ".row-state::before" in _css()


class TestDimIsOneToken:
    """'Soluk' icin on bir ayri opacity degeri vardi."""

    def test_no_raw_dimming(self):
        govde = _govde()
        # Animasyon kareleri disinda ham opacity kalmamali.
        temiz = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", govde, flags=re.S)
        ham = re.findall(r"opacity:\s*(\.\d+|0\.\d+);", temiz)
        assert not ham, "jeton disi opacity: " + repr(sorted(set(ham)))


class TestTheTopBarIsLaidOut:
    """Marka SABIT genislikte; bosluk sag kumeden once acilir.

    OLCULDU: `.brand { flex: 1 }` 1400px'te 1049px kapliyor ve "DeerX /
    proje" kirintisini ikiye bolup arada ~950px bosluk birakiyordu.
    """

    def test_the_brand_does_not_stretch(self):
        assert "flex: none" in _rule(".brand")

    def test_the_right_cluster_pushes_itself_right(self):
        assert "margin-left: auto" in _rule(".topbar-right")

    def test_the_active_rail_item_is_paper_coloured(self):
        """Etkin madde tezgahtan icerige bir sekme gibi baglanir; vurgu
        metinde degil inset seritte."""
        rule = _rule(".rail-item.is-active")
        assert "background: var(--surface)" in rule
        assert "box-shadow: inset" in rule
        assert "font-weight: 400" in rule
