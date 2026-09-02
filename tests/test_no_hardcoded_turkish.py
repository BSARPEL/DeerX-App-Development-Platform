"""Kullaniciya ya da modele giden hicbir metin kodda sabit kalmamali.

Ceviri bir kerelik bir is degil: bir sonraki ozellik yeni bir hata mesaji
getirir ve onu katalogdan gecirmek kolayca unutulur. O zaman arayuz
Ingilizce, yeni mesaj Turkce olur ve kimse fark etmez -- cunku hicbir sey
cokmez.

Bu dosya o unutmayi bir test hatasina cevirir. Kaynagi AST ile tarar ve
KULLANICIYA/MODELE giden cagrilarin sabit metin argumanlarina bakar:
hata firlatmalari, `console.print`, olay kayitlari, gunluk satirlari.

Yorumlar ve docstring'ler taranmaz -- onlar kodun kendi dili ve Turkce
kalmalari kasitli.

Yeni bir mesaj bu testi kirdiginda cozum:

    raise ToolError("Dosya bulunamadi")        # yanlis
    raise ToolError(t("fs.not_found"))         # dogru, karsiligi i18n.py'de
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

# Turkce olduguna isaret eden kokler. Ingilizce bir metinde sozcuk sinirlari
# icinde gecmezler.
TURKCE = re.compile(
    r"\b(bir|bu|ve|icin|ile|degil|yok|var|olarak|gore|sonra|once|daha|"
    r"calis\w*|dosya\w*|gorev\w*|hata\w*|kullan\w*|olustur\w*|baslat\w*|"
    r"deger\w*|gecer\w*|bulun\w*|yaz\w*|oku\w*|sec\w*|ver\w*|"
    r"gerek\w*|zorunlu|bekle\w*|dizin|anahtar|surec|ayar\w*|"
    r"guncelle\w*|tanimla\w*|sil\w*|kapat\w*|acil\w*|kosu|faz|"
    r"asim|izin|politika|reddedildi|desteklenmiyor|bilinmeyen)\b",
    re.I,
)

# Bu cagrilarin metin argumanlari kullaniciya ya da modele gider.
CIKIS_CAGRILARI = {
    # Hatalar
    "ToolError", "WorkspaceError", "ConfigError", "ApprovalDenied",
    "DeerXError", "BudgetExceeded", "LLMError", "AuthError", "RunBusy",
    # Sonuclar ve yanitlar. `cls` de sayilir: `ToolResult.error` kendi
    # sinifini `cls(...)` ile kuruyordu ve tarayici oradan gecen Turkce
    # oneki gormuyordu. `Adim` kurulum tablosunun satirlarini tasir.
    "ToolResult", "_error", "_fail", "cls", "Adim",
    # Ekran, olay akisi, gunluk
    "print", "emit", "info", "warning", "error", "debug", "exception",
    "append_note",
    # Teslimat hazirlik denetiminin satirlari; Ciktilar ekraninin en
    # ustunde durur. Listede olmadigi icin alti Turkce mesaj yillarca
    # gorulmedi -- tarayici bir CAGRI ADI listesine bakiyor ve o ad
    # burada yoktu.
    "ReadinessIssue",
}

# `ValueError` YALNIZCA web katmaninda sayilir. Oradaki ayar
# dogrulayicilari onu firlatiyor ve `update_settings` mesaji `_error()` ile
# AYNEN kullaniciya donduruyor: Ingilizce arayuzde "gecersiz deger 'vm'.
# Secenekler: host, docker" goruluyordu. Baska modullerdeki `ValueError`lar
# ic degismez ihlalleridir ("weights ve rankings uzunluklari esit olmali") --
# kullanici onlari hicbir zaman gormez ve katalogda yerleri yoktur.
WEB_KATMANI = {"app.py"}
CIKIS_CAGRILARI_WEB = CIKIS_CAGRILARI | {"ValueError"}

# Katalogun kendisi ve Ingilizce ceviriler dogal olarak metin icerir.
HARIC = {"i18n.py", "descriptions_en.py"}

KOK = pathlib.Path(__file__).resolve().parents[1] / "src" / "deerx"


def _sabit_parcalar(dugum: ast.AST):
    """f-string ve `+` birlestirmeleri icindeki sabit metin parcalari."""
    if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str):
        yield dugum.value
    elif isinstance(dugum, ast.JoinedStr):
        for parca in dugum.values:
            if isinstance(parca, ast.Constant) and isinstance(parca.value, str):
                yield parca.value
    elif isinstance(dugum, ast.BinOp) and isinstance(dugum.op, ast.Add):
        yield from _sabit_parcalar(dugum.left)
        yield from _sabit_parcalar(dugum.right)
    elif isinstance(dugum, (ast.List, ast.Tuple)):
        # `console.print(SEP.join([...]))` -- metin listenin icinde.
        for oge in dugum.elts:
            yield from _sabit_parcalar(oge)
    elif isinstance(dugum, ast.Call):
        # `SEP.join([...])` gibi sarmalayicilar.
        for arg in dugum.args:
            yield from _sabit_parcalar(arg)
    elif isinstance(dugum, ast.IfExp):
        yield from _sabit_parcalar(dugum.body)
        yield from _sabit_parcalar(dugum.orelse)


# Alti karakterden kisa ama tek basina kesin Turkce olan isaretciler.
# "HATA: " oneki tam da bu yuzden gozden kacmisti.
KISA_ISARETCILER = re.compile(r"\b(HATA|UYARI|TAMAM|BITTI|IPTAL)\b")


def _bulgular(yol: pathlib.Path) -> list[tuple[int, str]]:
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    aranan = (
        CIKIS_CAGRILARI_WEB
        if yol.name in WEB_KATMANI and yol.parent.name == "web"
        else CIKIS_CAGRILARI
    )
    bulunanlar = []
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Call):
            continue
        isim = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
        if isim not in aranan:
            continue
        # Konumsal VE anahtar kelimeli argumanlar. Yalnizca konumsala
        # bakmak buyuk bir korlukti: `ToolResult(content=...)` ve
        # `cls(content=...)` kod tabaninin her yerinde ve hicbiri
        # goruluyordu.
        argumanlar = list(dugum.args) + [k.value for k in dugum.keywords]
        for arg in argumanlar:
            for metin in _sabit_parcalar(arg):
                # Cok kisa parcalar ("ok", ": ") anlamli bir cumle tasimaz --
                # ama kisa bir isaretci ("HATA") tek basina Turkcedir.
                uzun_yeter = len(metin.strip()) >= 6
                if (uzun_yeter and TURKCE.search(metin)) or KISA_ISARETCILER.search(metin):
                    bulunanlar.append((dugum.lineno, metin.strip()[:80]))
    return bulunanlar


# SQL metninin ICINE gomulu cumleler. AST tarayicisi bunlari goremez:
# `execute("UPDATE ... error = \'Sunucu yeniden baslatildi\' ...")` cagrisinin
# argumani tek bir SQL dizesidir ve icindeki cumle onun bir parcasi. Oysa o
# cumle veritabanina yazilip kosu listesinde kullaniciya gosteriliyordu.
SQL_ICI_METIN = re.compile(r"'([^']{12,200})'")


def _sql_bulgulari(yol: pathlib.Path) -> list[tuple[int, str]]:
    bulunanlar = []
    for numara, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
        soyulmus = satir.strip()
        if soyulmus.startswith("#"):
            continue
        # Yalnizca SQL gorunumlu satirlar: icinde bir SQL anahtar sozcugu
        # ve tek tirnakli bir metin olan satirlar.
        if not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|SET|WHERE|VALUES)\b", satir):
            continue
        for m in SQL_ICI_METIN.finditer(satir):
            if TURKCE.search(m.group(1)):
                bulunanlar.append((numara, m.group(1)[:80]))
    return bulunanlar


KAYNAKLAR = sorted(p for p in KOK.rglob("*.py") if p.name not in HARIC)


@pytest.mark.parametrize("yol", KAYNAKLAR, ids=lambda p: p.name)
def test_no_hardcoded_turkish(yol: pathlib.Path):
    bulunanlar = _bulgular(yol)
    rapor = "\n".join(f"  {yol.name}:{satir}  {metin}" for satir, metin in bulunanlar)
    assert not bulunanlar, (
        f"Katalogdan gecmeyen {len(bulunanlar)} metin:\n{rapor}\n"
        "Karsiligini `src/deerx/i18n.py` icine ekleyip `t(\"anahtar\")` kullanin."
    )


@pytest.mark.parametrize("yol", KAYNAKLAR, ids=lambda p: p.name)
def test_no_turkish_buried_in_sql(yol: pathlib.Path):
    bulunanlar = _sql_bulgulari(yol)
    rapor = "\n".join(f"  {yol.name}:{satir}  {metin}" for satir, metin in bulunanlar)
    assert not bulunanlar, (
        f"SQL metninin icine gomulu {len(bulunanlar)} Turkce cumle:\n{rapor}\n"
        "Metni parametre olarak gecirin: `error = ?` + `t(\"anahtar\")`."
    )


def test_the_sql_scan_would_actually_catch_something():
    """SQL tarayicisi da sessizce ise yaramaz hale gelebilir."""
    import tempfile

    ornek = (
        'conn.execute("UPDATE runs SET error = '
        + chr(39)
        + "Sunucu yeniden baslatildi; kosu kesildi."
        + chr(39)
        + ' WHERE id = ?")'
    )
    with tempfile.TemporaryDirectory() as d:
        sahte = pathlib.Path(d) / "sahte.py"
        sahte.write_text(ornek + "\n", encoding="utf-8")
        assert _sql_bulgulari(sahte), "SQL tarayicisi artik yakalamiyor"


def test_the_scan_would_actually_catch_something():
    """Tarayici bozulursa bu dosya sessizce hicbir sey test etmez hale gelir.

    Butun desenler bir gun yanlislikla silinebilir ya da `CIKIS_CAGRILARI`
    bosalabilir; o zaman her dosya temiz gorunur. Bu test tarayicinin
    hala isledigini dogrular.
    """
    ornek = ast.parse('raise ToolError(f"{path} bulunamadi, dosya yok")')
    bulunanlar = []
    for dugum in ast.walk(ornek):
        if isinstance(dugum, ast.Call):
            isim = getattr(dugum.func, "id", None)
            if isim in CIKIS_CAGRILARI:
                for arg in dugum.args:
                    for metin in _sabit_parcalar(arg):
                        if len(metin.strip()) >= 6 and TURKCE.search(metin):
                            bulunanlar.append(metin)
    assert bulunanlar, "tarayici artik Turkce metin yakalamiyor"


def test_the_scan_covers_the_package():
    """Yol yanlissa tarama bos bir dosya kumesinde kosar ve hep gecer."""
    assert len(KAYNAKLAR) > 30, f"yalnizca {len(KAYNAKLAR)} dosya tarandi"
