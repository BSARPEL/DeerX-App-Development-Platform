"""DeerX'in KENDI bilgi tabanini kurar.

Neden: bu depo 7.500 satir belge ve 16.000 satir kod tasiyor. Bir soruya
cevap ararken -- kendi ajanlarimiz da dahil -- dosya dosya gezmek yerine
aranabilir bir tabana bakmak gerekiyor. DeerX zaten bir RAG motoru
tasiyor; burada onu KENDI uzerine dogrultuyoruz.

Ne indekslenir ve NEDEN:

    README*.md      giris noktasi; projenin ne oldugu
    docs/           iki dilli anlatim: kurulum, mimari, guvenlik, CLI
    src/deerx/      kodun kendisi. Yorumlar KARARLARI anlatiyor
                    ("neden boyle" sorusunun cevabi cogu zaman burada)
    tests/          en iyi belge: her test gercek bir hatanin karsiligi
                    ve docstring'i o hatayi anlatiyor
    scripts/        kurulum, baslatma, ekran goruntusu betikleri
    examples/       ornek sartname

Ne indekslenmez: `.venv`, `__pycache__`, `docs/images` (ikili), ve
uretilen her sey. Bir bilgi tabani, icinde ne oldugunu bilmediginiz
seyler oldugunda ise yaramaz.

Kullanim:

    uv run python scripts/knowledge/build.py            # varsayilan yer
    uv run python scripts/knowledge/build.py --hedef D:/deerx-kb
    uv run python scripts/knowledge/build.py --hizli    # gomme modeli indirmeden
    uv run python scripts/knowledge/build.py --force    # bastan indeksle

Sorgulamak icin: scripts/knowledge/ask.py, ya da

    cd .deerx-kb && uv run deerx search "sandbox nasil calisiyor"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

DEPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEPO / "src"))

from deerx.config import load_settings  # noqa: E402
from deerx.rag.knowledge import KnowledgeBase  # noqa: E402

VARSAYILAN_HEDEF = DEPO / ".deerx-kb"

# Indekslenecek yollar. Liste ACIK: bir depoyu bastan sona taramak,
# icine ne girdigini kimsenin bilmedigi bir taban uretir.
KAYNAKLAR = [
    "README.md",
    "README.tr.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs",
    "src/deerx",
    "tests",
    "scripts",
    "examples",
]

AYAR_SABLONU = """# DeerX'in kendi bilgi tabani. `scripts/knowledge/build.py` uretti.
#
# Bu bir CALISMA ALANI degil, bir TABAN: icinde bir proje gelistirilmiyor,
# yalnizca DeerX'in kendi belgeleri ve kodu aranabilir halde duruyor.

[deerx]
language = "tr"

[deerx.rag]
{gomme}
# Kod ve belge parcalari icin daha genis pencere: bir fonksiyonun ya da
# bir bolumun ortasindan kesilen parca, tek basina okundugunda anlamini
# kaybediyor.
chunk_tokens = 900
chunk_overlap_tokens = 150
top_k = 8

# OLCULDU: bu iki dosya indekslendiginde "denetim gunlugu" sorgusunun
# ilk sirasina `index.html` cikiyordu -- "## Denetim gunlugu Kullanici
# Islem Satir 50 200 1000 Yenile" gibi, isaretlemeden arta kalan
# sozcuk yiginı. Sozluk dosyasi da ayni: 1400 satir anahtar-deger,
# her sorguya biraz benziyor, hicbirini cevaplamiyor.
exclude_globs = [
    "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
    "**/__pycache__/**", "**/dist/**", "**/build/**", "**/.deerx/**",
    "**/*.min.js", "**/*.lock", "**/package-lock.json", "**/uv.lock",
    "**/static/index.html",
    "**/static/i18n.js",
    "**/docs/images/**",
]
"""

GERCEK_GOMME = """embedding_provider = "fastembed"
embedding_model = "intfloat/multilingual-e5-large"
embedding_dim = 1024"""

HIZLI_GOMME = """# `--hizli`: model indirmeden. Sozcuksel arama tam calisir, anlamsal
# arama zayiftir -- gercek kullanim icin `fastembed` tercih edin.
embedding_provider = "hash"
embedding_dim = 128"""


def kur(hedef: Path, *, hizli: bool, force: bool) -> int:
    hedef.mkdir(parents=True, exist_ok=True)
    ayar = hedef / "deerx.toml"
    ayar.write_text(
        AYAR_SABLONU.format(gomme=HIZLI_GOMME if hizli else GERCEK_GOMME),
        encoding="utf-8",
        newline="\n",
    )

    settings = load_settings(hedef)
    settings.ensure_dirs()
    kb = KnowledgeBase(settings)

    print(f"taban   : {hedef}")
    print(f"gomme   : {settings.rag.embedding_provider} / {settings.rag.embedding_dim}")
    print()

    toplam_dosya = toplam_parca = atlanan = hatali = 0
    basladi = time.time()
    for ad in KAYNAKLAR:
        yol = DEPO / ad
        if not yol.exists():
            print(f"  {ad:<16} yok, atlandi")
            continue
        sonuclar = kb.ingest_path(yol, force=force)
        parca = sum(r.chunks for r in sonuclar)
        hata = [r for r in sonuclar if r.error]
        bos = [r for r in sonuclar if not r.error and r.chunks == 0]
        toplam_dosya += len(sonuclar) - len(hata)
        toplam_parca += parca
        atlanan += len(bos)
        hatali += len(hata)
        print(f"  {ad:<16} {len(sonuclar):>4} dosya  {parca:>5} parca"
              + (f"  ({len(hata)} hata)" if hata else ""))
        for r in hata[:3]:
            print(f"      ! {Path(r.source).name}: {r.error}")

    sure = time.time() - basladi
    istatistik = kb.stats()
    print()
    print(f"toplam  : {toplam_dosya} dosya, {toplam_parca} parca, {sure:.0f} sn")
    print(f"tabanda : {istatistik['documents']} dokuman, {istatistik['chunks']} parca")
    if atlanan:
        print(f"atlanan : {atlanan} dosya bos ya da degismemis")
    if hatali:
        print(f"hatali  : {hatali} dosya okunamadi")
    print()
    print("Sorgulamak icin:")
    print('  uv run python scripts/knowledge/ask.py "sandbox nasil calisiyor"')
    print(f"  cd {hedef.name} && uv run deerx search \"denetim gunlugu\"")
    kb.close()
    return 0 if not hatali else 1


def main() -> int:
    ayristirici = argparse.ArgumentParser(description=__doc__)
    ayristirici.add_argument("--hedef", type=Path, default=VARSAYILAN_HEDEF,
                             help="bilgi tabaninin yeri")
    ayristirici.add_argument("--hizli", action="store_true",
                             help="gomme modeli indirmeden (zayif anlamsal arama)")
    ayristirici.add_argument("--force", action="store_true",
                             help="degismemis dosyalari da yeniden indeksle")
    arg = ayristirici.parse_args()
    return kur(arg.hedef.resolve(), hizli=arg.hizli, force=arg.force)


if __name__ == "__main__":
    raise SystemExit(main())
