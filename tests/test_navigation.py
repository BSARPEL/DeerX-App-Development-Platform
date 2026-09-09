"""Her ekrana bir yol var mi -- ve o yol her genislikte duruyor mu?

Bu dosya bir gerilemeden dogdu. Projeler ve Ayarlar maddeleri sol rayin
dibindeki `.rail-foot` kutusuna tasindi (dogru gerekce: o iki ekran
projeye degil, platforma ve hesaba ait), ama 820 pikselin altinda o
kutunun TAMAMI `display: none` idi. Sonuc: telefonda kullanici proje
kaydedemiyor, model saglayicisini goremiyor, parolasini degistiremiyordu.

Ekranin var olmasi yetmez; oraya GOTUREN bir denetim de olmali ve o
denetim her genislikte gorunmeli. Iki testin olctugu tam olarak bu.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from deerx.web.app import STATIC_DIR


def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# Kapanisi olmayan etiketler: yigina itilirlerse butun agac kayar.
BOSLAR = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _AtaZinciri(HTMLParser):
    """`data-view` tasiyan her dugumun ATA ZINCIRINI toplar.

    Zincirin her halkasi (etiket, id, sinif kumesi) uclusudur; bir CSS
    kuralinin o dugumu gizleyip gizlemedigini anlamak icin gereken tek
    sey bu.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.yigin: list[tuple[str, str, frozenset[str]]] = []
        self.zincirler: dict[str, list[tuple[str, str, frozenset[str]]]] = {}

    def handle_starttag(self, tag, attrs):
        oz = dict(attrs)
        halka = (tag, oz.get("id") or "", frozenset((oz.get("class") or "").split()))
        if oz.get("data-view"):
            self.zincirler[oz["data-view"]] = [*self.yigin, halka]
        if tag not in BOSLAR:
            self.yigin.append(halka)

    def handle_startendtag(self, tag, attrs):
        oz = dict(attrs)
        if oz.get("data-view"):
            halka = (tag, oz.get("id") or "",
                     frozenset((oz.get("class") or "").split()))
            self.zincirler[oz["data-view"]] = [*self.yigin, halka]

    def handle_endtag(self, tag):
        for i in range(len(self.yigin) - 1, -1, -1):
            if self.yigin[i][0] == tag:
                del self.yigin[i:]
                return


def _gorunum_zincirleri() -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    ayristirici = _AtaZinciri()
    ayristirici.feed(_asset("index.html"))
    return ayristirici.zincirler


def _views() -> list[str]:
    """app.js'teki `VIEWS` dizisi -- yonlendirmenin kabul ettigi adlar."""
    js = _asset("app.js")
    govde = js.split("const VIEWS = [", 1)[1].split("]", 1)[0]
    return re.findall(r'"([^"]+)"', govde)


def _dar_ekran_kurallari() -> list[tuple[str, str]]:
    """820 pikselin altindaki medya blogundaki (secici, govde) ciftleri."""
    css = _asset("styles.css")
    bas = css.index("@media (max-width: 820px) {")
    # Blogun sonu: suslu parantezleri sayarak. `index("}")` ilk ic kuralda
    # durur ve blogun yarisini kacirirdi.
    derinlik, i = 0, bas
    while i < len(css):
        if css[i] == "{":
            derinlik += 1
        elif css[i] == "}":
            derinlik -= 1
            if derinlik == 0:
                break
        i += 1
    blok = css[bas:i]
    # Yorumlar ayiklanir: icinde suslu parantez ya da noktali virgul olan
    # bir yorum ayristirmayi bozar.
    blok = re.sub(r"/\*.*?\*/", "", blok, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", blok.split("{", 1)[1])


def _en_sagdaki(secici: str) -> tuple[str, str, set[str]] | None:
    """Seciciyi en sagdaki bilesik parcasina indirger.

    Bir kural yalnizca EN SAGDAKI parcasinin esledigi dugumu bicimler;
    soldakiler ata kosuludur. `.rail-foot .rail-setting` kuralinin
    gizledigi sey `.rail-setting`tir, `.rail-foot` degil.
    """
    secici = secici.strip()
    if not secici or ":" in secici:
        return None          # sozde sinif: dugumun kendisini olcemeyiz
    parca = re.split(r"[\s>+~]+", secici)[-1]
    etiket = re.match(r"^[a-zA-Z][\w-]*", parca)
    return (
        etiket.group(0) if etiket else "",
        (re.search(r"#([\w-]+)", parca) or [None, ""])[1],
        set(re.findall(r"\.([\w-]+)", parca)),
    )


class TestHerEkraninBirKapisiVar:
    def test_every_view_has_a_control_that_opens_it(self):
        """`VIEWS`teki her ad icin bir `data-view` tetikleyicisi olmali.

        Yonlendirmenin kabul ettigi ama hicbir dugmenin acmadigi bir ekran,
        yalnizca adresi elle yazanlarin bulabildigi bir ekrandir.
        """
        zincirler = _gorunum_zincirleri()
        eksik = [ad for ad in _views() if ad not in zincirler]
        assert not eksik, f"bu ekranlara goturen denetim yok: {eksik}"

    def test_the_narrow_screen_hides_no_navigation(self):
        """820 pikselin altinda hicbir gezinti maddesi gizlenmemeli.

        Kural ATA ZINCIRINE bakar: maddenin kendisini gizlemek de,
        icinde durdugu kutuyu gizlemek de ayni sonucu verir.
        """
        zincirler = _gorunum_zincirleri()
        suclu: list[str] = []
        for secici, govde in _dar_ekran_kurallari():
            if not re.search(r"display\s*:\s*none", govde):
                continue
            for parca in secici.split(","):
                hedef = _en_sagdaki(parca)
                if hedef is None:
                    continue
                etiket, kimlik, siniflar = hedef
                for ad, zincir in zincirler.items():
                    for h_etiket, h_kimlik, h_siniflar in zincir:
                        if etiket and etiket != h_etiket:
                            continue
                        if kimlik and kimlik != h_kimlik:
                            continue
                        if not siniflar <= h_siniflar:
                            continue
                        suclu.append(f"{parca.strip()} -> {ad}")
        assert not suclu, (
            "dar ekranda su gezinti maddeleri gizleniyor ve baska yolu yok: "
            + ", ".join(sorted(set(suclu)))
        )

    def test_projects_and_settings_have_no_second_door(self):
        """Iki ekranin TEK kapisi oldugunu civiler.

        Ustteki testin neden gevsetilemeyecegini soyleyen test bu: bir gun
        "nasil olsa baska bir yerden de girilir" diye dusunulurse, bu test
        o varsayimin yanlis oldugunu gosterir. Ikinci bir yol ACILIRSA test
        duser ve o zaman ustteki kural bilerek gevsetilebilir.
        """
        html = _asset("index.html")
        for ad in ("projects", "settings"):
            assert html.count(f'data-view="{ad}"') == 1, (
                f"{ad} ekranina birden fazla kapi var; dar ekran kurali "
                "yeniden degerlendirilebilir"
            )
