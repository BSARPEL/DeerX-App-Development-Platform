"""Dokumantasyonun kendi butunlugu.

Otuz kusur markdown dosyasi birbirine link veriyor ve ikisi ayni belgenin
iki dili. Bir dosya tasindiginda ya da yeniden adlandirildiginda kirilan
linkleri kimse fark etmez: markdown sessizce olu bir link isler ve GitHub
onu tiklanabilir gosterir.

Bu dosya o sessizligi bir test hatasina cevirir.
"""

from __future__ import annotations

import pathlib
import re

import pytest

KOK = pathlib.Path(__file__).resolve().parents[1]
DOCS = KOK / "docs"

# Kod bloklarindaki `[...](...)` ornekleri link degil.
KOD_BLOGU = re.compile(r"```.*?```", re.S)
SATIR_ICI_KOD = re.compile(r"`[^`]*`")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Iki dilin de ayni dosyalari tasimasi gereken kume.
SAYFALAR = (
    "README", "getting-started", "pipeline", "providers", "web-ui", "cli",
    "configuration", "tools", "architecture", "security", "delivery",
    "mcp", "i18n", "verification", "troubleshooting", "extending",
)


def _markdown_dosyalari() -> list[pathlib.Path]:
    dosyalar = sorted(DOCS.rglob("*.md"))
    dosyalar += [KOK / ad for ad in ("README.md", "README.tr.md",
                                     "CONTRIBUTING.md", "SECURITY.md")]
    return [p for p in dosyalar if p.is_file()]


def _linkler(yol: pathlib.Path) -> list[str]:
    metin = yol.read_text(encoding="utf-8")
    metin = KOD_BLOGU.sub("", metin)
    metin = SATIR_ICI_KOD.sub("", metin)
    return LINK.findall(metin)


DOSYALAR = _markdown_dosyalari()


@pytest.mark.parametrize("yol", DOSYALAR, ids=lambda p: p.name)
def test_relative_links_resolve(yol: pathlib.Path):
    """Goreli her link gercek bir dosyayi gostermeli."""
    kirik = []
    for hedef in _linkler(yol):
        if hedef.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # `dosya.md#bolum` -> dosya kismi
        parca = hedef.split("#", 1)[0]
        if not parca:
            continue
        if not (yol.parent / parca).resolve().exists():
            kirik.append(hedef)
    assert not kirik, f"{yol.name}: cozulmeyen link: {kirik}"


@pytest.mark.parametrize("sayfa", SAYFALAR)
def test_both_languages_have_the_page(sayfa: str):
    """Bir dilde olup digerinde olmayan sayfa, yarim kalmis bir cevirdir."""
    assert (DOCS / f"{sayfa}.md").is_file(), f"docs/{sayfa}.md yok"
    assert (DOCS / "tr" / f"{sayfa}.md").is_file(), f"docs/tr/{sayfa}.md yok"


def test_no_extra_pages():
    """Bir dile eklenip digerine eklenmemis sayfa da ayni bosluktur."""
    ingilizce = {p.stem for p in DOCS.glob("*.md")}
    turkce = {p.stem for p in (DOCS / "tr").glob("*.md")}
    # CONTRIBUTING yalnizca Turkce tarafinda; Ingilizcesi kokte duruyor.
    turkce.discard("CONTRIBUTING")
    assert ingilizce == turkce, f"fark: {ingilizce ^ turkce}"


@pytest.mark.parametrize("yol", sorted(DOCS.rglob("*.md")), ids=lambda p: str(p.name))
def test_every_doc_links_to_its_translation(yol: pathlib.Path):
    """Her sayfa diger dildeki esine goturmeli.

    Iki dilli bir belgede en cok kaybolan sey, digerine gecis yolu.
    """
    metin = yol.read_text(encoding="utf-8")
    assert re.search(r"\[(English|Türkçe)\]\(", metin), (
        f"{yol.name}: diger dile link yok"
    )


@pytest.mark.parametrize("yol", DOSYALAR, ids=lambda p: p.name)
def test_no_personal_absolute_paths(yol: pathlib.Path):
    """Yayimlanan bir belgede birinin ev dizini ornek olamaz."""
    metin = yol.read_text(encoding="utf-8")
    bulunanlar = re.findall(r"[A-Za-z]:[\\/]Users[\\/][^\s`\"')]+", metin)
    bulunanlar += re.findall(r"/home/[a-z][^\s`\"')]*", metin)
    assert not bulunanlar, f"{yol.name}: kisisel mutlak yol: {bulunanlar}"


# `<img src="...">` etiketleri. Markdown link tarayicisi bunlari gormez ve
# README'nin galerisi tamamen HTML etiketlerinden olusuyor: bir dosya adi
# yanlis yazilsa GitHub'da kirik goruntu kutusu cikardi.
IMG = re.compile(r'<img[^>]+src="([^"]+)"')


@pytest.mark.parametrize("yol", DOSYALAR, ids=lambda p: p.name)
def test_image_tags_resolve(yol: pathlib.Path):
    kirik = []
    for hedef in IMG.findall(yol.read_text(encoding="utf-8")):
        if hedef.startswith(("http://", "https://", "data:")):
            continue
        dosya = (yol.parent / hedef).resolve()
        if not dosya.is_file() or dosya.stat().st_size == 0:
            kirik.append(hedef)
    assert not kirik, f"{yol.name}: bulunamayan goruntu: {kirik}"


def test_every_screenshot_is_used():
    """Kullanilmayan bir ekran goruntusu depoda olu agirliktir.

    Ayrica ters yonu de tutar: bir goruntu yeniden adlandirilinca hem
    dosyanin hem de ona bakan sayfanin guncellenmesi gerekir.
    """
    gorseller = {p.name for p in (DOCS / "images").glob("*.png")}
    assert gorseller, "docs/images bos"
    kullanilan = set()
    for yol in DOSYALAR:
        metin = yol.read_text(encoding="utf-8")
        for hedef in IMG.findall(metin) + LINK.findall(metin):
            kullanilan.add(hedef.rsplit("/", 1)[-1])
    olu = sorted(gorseller - kullanilan)
    assert not olu, f"hicbir sayfanin gostermedigi goruntu: {olu}"


def test_both_languages_have_screenshots():
    """Arayuz iki dilli; ekran goruntuleri de oyle olmali.

    Ingilizce bir ekran goruntusunu Turkce sayfaya koymak, o sayfayi
    okuyan icin ceviriyi yarim birakmak olurdu.
    """
    gorseller = {p.stem for p in (DOCS / "images").glob("*.png")}
    tek_dilli = sorted(
        ad for ad in gorseller
        if ad.endswith("-en") and f"{ad[:-3]}-tr" not in gorseller
    )
    assert not tek_dilli, f"Turkcesi olmayan ekran goruntusu: {tek_dilli}"


# Yayimlanan her kaynak dosya: belgeler zaten taraniyordu, betikler
# taranmiyordu. Ekran goruntusu betikleri benim makinemde yazildi ve
# yollarini oradan aliyorlardi -- baska bir makinede calismazlardi.
KISISEL_YOL = re.compile(
    r"[A-Za-z]:[\\/]Users[\\/][^\s`\"')]+" r"|/home/[a-z][^\s`\"')]*"
)
KAYNAK_DOSYALARI = sorted(
    p for p in list((KOK / "scripts").rglob("*")) + list((KOK / "src").rglob("*.py"))
    if p.is_file() and p.suffix in {".py", ".sh", ".ps1", ".cmd", ".md"}
)


@pytest.mark.parametrize(
    "yol", KAYNAK_DOSYALARI, ids=lambda p: str(p.relative_to(KOK)).replace("\\", "/")
)
def test_no_personal_paths_in_scripts(yol: pathlib.Path):
    """Kodun bagli oldugu bir yol, yazanin makinesine ozgu olamaz.

    YORUM SATIRLARI atlanir. `deerx.ps1` icinde bosluklu bir yolun neden
    tirnaklanmasi gerektigini anlatan bir ornek var ("C:/Users/Ada
    Lovelace/proje") ve o ornek dogru; kotu olan, calisan kodun boyle bir
    yola BAGLI olmasidir.
    """
    bulunanlar = []
    for numara, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
        if satir.lstrip().startswith(("#", "//", "rem ", "REM ")):
            continue
        for bulgu in KISISEL_YOL.findall(satir):
            bulunanlar.append(f"{numara}: {bulgu}")
    assert not bulunanlar, f"{yol.name}: kisisel mutlak yol: {bulunanlar}"


def test_the_source_scan_is_not_empty():
    assert len(KAYNAK_DOSYALARI) >= 20, f"yalnizca {len(KAYNAK_DOSYALARI)} dosya"


def test_the_readmes_point_at_each_other():
    assert "README.tr.md" in (KOK / "README.md").read_text(encoding="utf-8")
    assert "README.md" in (KOK / "README.tr.md").read_text(encoding="utf-8")


def test_the_scan_covers_the_docs():
    """Yol yanlissa tarama bos bir kumede kosar ve hep gecer."""
    assert len(DOSYALAR) >= 30, f"yalnizca {len(DOSYALAR)} dosya tarandi"


class TestOutlineParity:
    """Ayni sayfanin iki dili ayni belge olmali.

    Biri guncellenip digeri unutulursa, o dili okuyan kullanici eksik bir
    belge okur ve bunu anlamasinin bir yolu yoktur -- iki dosyayi yan yana
    koymadikca.
    """

    @staticmethod
    def _basliklar(yol: pathlib.Path) -> list[int]:
        """Baslik derinlikleri, sirayla. Kod bloklari haric."""
        disinda, icinde = [], False
        for satir in yol.read_text(encoding="utf-8").splitlines():
            if satir.startswith("```"):
                icinde = not icinde
            elif not icinde:
                disinda.append(satir)
        return [
            len(m.group(1))
            for satir in disinda
            if (m := re.match(r"^(#{1,4}) \S", satir))
        ]

    @pytest.mark.parametrize("sayfa", SAYFALAR)
    def test_the_two_languages_have_the_same_outline(self, sayfa: str):
        ingilizce = self._basliklar(DOCS / f"{sayfa}.md")
        turkce = self._basliklar(DOCS / "tr" / f"{sayfa}.md")
        assert ingilizce == turkce, (
            f"{sayfa}: {len(ingilizce)} baslik (en) / {len(turkce)} (tr)"
        )

    def test_the_readmes_have_the_same_outline(self):
        assert self._basliklar(KOK / "README.md") == self._basliklar(
            KOK / "README.tr.md"
        )


def test_the_documented_test_count_is_current(pytestconfig):
    """Dokumandaki test sayisi gercekle ayni olmali.

    Bayatlamaya musait bir sayi bir kez bayatladi: Turkce README 997 test
    varken 558 diyordu. Sayi artik yalnizca iki dogrulama sayfasinda ve bu
    test onu gercekle karsilastiriyor. Test ekleyen kisi iki satir
    guncelleyecek -- bunun alternatifi, kimsenin fark etmedigi yanlis bir
    sayiyla yayimlamak.
    """
    # Sayim yalnizca TAM suit kosarken anlamlidir. Alt kume kosuldugunda
    # (`pytest tests/test_docs.py`) toplanan test sayisi dogal olarak
    # farklidir; onu dokumanla karsilastirmak yanlis alarm olurdu.
    if pytestconfig.option.file_or_dir or pytestconfig.option.keyword:
        pytest.skip("alt kume kosuluyor; sayim yalnizca tam suitte anlamli")

    gercek = pytestconfig.pluginmanager.getplugin("terminalreporter")._numcollected

    sayfalar = {
        DOCS / "verification.md": rf"\*\*{gercek} tests pass\*\*",
        DOCS / "tr" / "verification.md": rf"\*\*{gercek} test geçiyor\*\*",
    }
    for yol, desen in sayfalar.items():
        metin = yol.read_text(encoding="utf-8")
        yazan = re.search(r"\*\*(\d+) (?:tests pass|test geçiyor)\*\*", metin)
        assert yazan, f"{yol.name}: test sayisi bulunamadi"
        assert re.search(desen, metin), (
            f"{yol.name}: {yazan.group(1)} yaziyor, gercek {gercek}"
        )


def test_the_documented_tool_counts_match_the_code() -> None:
    """`tools.md` her rolun arac SAYISINI yaziyor; kod degisince kayiyor.

    Bugun yasandi: Mockup rolune iki gorsel araci eklendi, tablo 8'de kaldi.
    Ayni anda "acik web'e yalnizca Arastirmaci ulasir" cumlesi de yanlisa
    dondu. Elle tutulan sayilar sessizce eskiyor; bu test onlari koda baglar.
    """
    import re

    from deerx.tools import TOOLSETS

    BASLIKLAR = {
        "analyst": "Analyst", "researcher": "Researcher", "assessor": "Assessor",
        "mockup": "Mockup", "architect": "Architect", "planner": "Planner",
        "backend": "Backend", "frontend": "Frontend", "qa": "QA",
        "reviewer": "Reviewer", "staging": "Staging", "live": "Live",
    }
    metin = (DOCS / "tools.md").read_text(encoding="utf-8")
    yanlis = []
    for anahtar, baslik in BASLIKLAR.items():
        desen = r"^\|\s*" + re.escape(baslik) + r"\s*\|\s*(\d+)\s*\|"
        m = re.search(desen, metin, re.M)
        if m is None:
            yanlis.append(baslik + ": tabloda bulunamadi")
        elif int(m.group(1)) != len(TOOLSETS[anahtar]):
            yanlis.append(
                f"{baslik}: belgede {m.group(1)}, kodda {len(TOOLSETS[anahtar])}"
            )
    assert not yanlis, "tools.md arac sayilari eskimis:\n  " + "\n  ".join(yanlis)


def test_the_documented_tool_total_matches_the_code() -> None:
    """Toplam arac sayisi sekiz ayri yerde yaziyor.

    Bugun iki arac eklendi ve "34" yazan her satir yanlisa dondu: indeks,
    tools.md, i18n.md, architecture.md ve iki README -- iki dilde. Elle
    tutulan bir sayinin sekiz kopyasi varsa eskimemesi mumkun degil.
    """
    import re

    from deerx.tools import build_registry

    gercek = len(build_registry().names())
    # Yalnizca TOPLAMI anlatan kaliplar. "10 tools x 24K" gibi baska bir
    # baglamdaki sayiyi yakalamak yanlis alarm olurdu -- ilk yazimda oldu.
    desen = re.compile(
        r"(?:All |There are |├── tools/\s+)(\d+)\s+(?:agent )?tools?\b"
        r"|(\d+)\s+(?:ajan )?ara[cç](?:ın tamamı| var)"
        r"|\|\s*(\d+)\s+(?:tools|araç)\s*\|"
    )
    yanlis = []
    for yol in sorted(DOCS.rglob("*.md")) + [KOK / "README.md", KOK / "README.tr.md"]:
        for m in desen.finditer(yol.read_text(encoding="utf-8")):
            sayi = next(g for g in m.groups() if g)
            if int(sayi) != gercek:
                yanlis.append(f"{yol.name}: '{m.group(0).strip()}' (kodda {gercek})")
    assert not yanlis, "belgelerdeki arac sayisi eskimis:\n  " + "\n  ".join(yanlis)


def test_the_version_is_the_same_in_both_places() -> None:
    """Surum iki yerde yaziyor ve ikisi ayrisabilir.

    `pyproject.toml` tekerlegin adini ve PyPI kaydini belirler;
    `deerx.__version__` ise calisan kurulumun kendisi hakkinda soyledigi
    sey -- hata raporlarinda, `--version` ciktisinda ve destek
    konusmalarinda gorunen sayi budur.

    Ayrisirlarsa kimse fark etmez: ikisi de gecerli bir dize, hicbir sey
    cokmez. Yalnizca kullanicinin bildirdigi surum, yayimlanan surum
    olmaz -- ve hata hangi kodda arayacaginizi soylemek yerine yanlis
    yere gonderir.
    """
    import tomllib

    import deerx

    with (KOK / "pyproject.toml").open("rb") as fh:
        yazan = tomllib.load(fh)["project"]["version"]

    assert deerx.__version__ == yazan, (
        f"deerx.__version__ = {deerx.__version__!r}, "
        f"pyproject.toml = {yazan!r}"
    )
