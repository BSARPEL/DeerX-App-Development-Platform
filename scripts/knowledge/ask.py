"""Bilgi tabanina soru sorar: once ara, sonra modele okut.

`build.py` tabani kurar; bu betik onu bir LLM'in KULLANABILECEGI hale
getirir. Yaptigi is uc adim:

  1. Hibrit arama (anlamsal + sozcuksel, RRF ile birlestirilmis)
  2. Bulunan parcalari KAYNAKLARIYLA birlikte tek bir baglama dizer
  3. Modele "yalnizca bunlara dayanarak cevapla, kaynak goster" der

Uc kural bilincli:

* Model yalnizca verilen parcalardan cevaplar. Bir belge tabaninin
  degeri, cevabin nereden geldigini gosterebilmesinde; "bildigim
  kadariyla" diyen bir cevap, tabanin hic sorgulanmamasiyla ayni.
* Bulunamayan sey SOYLENIR. Uydurulmus bir cevap, yanlis cevaptan
  daha pahalidir: yanlis oldugunu anlamak icin dogrusunu bilmek
  gerekir.
* Kaynaklar cevabin altinda ayrica listelenir; modelin atif yapip
  yapmamasindan bagimsiz olarak kullanici nereye bakacagini bilir.

Kullanim:

    uv run python scripts/knowledge/ask.py "denetim gunlugu ne kaydediyor"
    uv run python scripts/knowledge/ask.py "sandbox" --ayar ./demo
    uv run python scripts/knowledge/ask.py "onay kipi" --sadece-arama
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from pathlib import Path

DEPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEPO / "src"))

from deerx.config import load_settings  # noqa: E402
from deerx.rag.knowledge import KnowledgeBase  # noqa: E402

VARSAYILAN_TABAN = DEPO / ".deerx-kb"

SISTEM = """Sen DeerX projesinin belgelerini ve kodunu okuyan bir yardimcisin.

Kurallar:

1. YALNIZCA sana verilen alintilara dayanarak cevapla. Genel bilgine
   ya da baska projelere dayanma.
2. Alintilarda cevap YOKSA acikca soyle: "Bu bilgi tabanda yok."
   Tahmin etme, tamamlama.
3. Her iddiayi kaynagiyla ver: [dosya adi] bicimde koseli parantez
   icinde. Birden fazla kaynak varsa hepsini yaz.
4. Kisa ve dogrudan ol. Soru "nasil" ise adimlari sirala; "neden" ise
   gerekceyi ver.
5. Kod alintisi gerekiyorsa kisa tut ve nereden geldigini soyle.
"""

SORU_SABLONU = """Asagida DeerX'in bilgi tabanindan alinan {n} alinti var.

{alintilar}

--- SORU ---
{soru}
"""


def baglam_kur(parcalar, azami_karakter: int) -> tuple[str, list[str]]:
    """Alintilari tek metne dizer; butceyi asanlari BIRAKIR.

    Kirpmak yerine birakiyoruz: yarim kesilmis bir alinti, modele
    tamam gibi gorunur ve eksik bilgiden emin bir cevap uretir.
    """
    parcalar_metni: list[str] = []
    kaynaklar: list[str] = []
    kullanilan = 0
    for i, p in enumerate(parcalar, start=1):
        govde = f"[{i}] {p.citation()}\n{p.text}\n"
        if kullanilan + len(govde) > azami_karakter and parcalar_metni:
            break
        parcalar_metni.append(govde)
        kaynaklar.append(f"[{i}] {p.citation()}  (skor {p.score:.3f})")
        kullanilan += len(govde)
    return "\n".join(parcalar_metni), kaynaklar


def sor(
    soru: str, *, taban: Path, ayar_alani: Path | None, k: int,
    sadece_arama: bool, azami_karakter: int,
) -> int:
    if not (taban / ".deerx" / "deerx.db").is_file():
        print(f"HATA: {taban} icinde bilgi tabani yok.", file=sys.stderr)
        print("Once kurun:  uv run python scripts/knowledge/build.py", file=sys.stderr)
        return 1

    taban_ayari = load_settings(taban)
    kb = KnowledgeBase(taban_ayari)

    basladi = time.time()
    parcalar = kb.search(soru, k=k)
    arama_suresi = time.time() - basladi
    if not parcalar:
        print("Tabanda bu soruyla eslesen parca yok.")
        kb.close()
        return 2

    baglam, kaynaklar = baglam_kur(parcalar, azami_karakter)
    print(f"{len(parcalar)} parca bulundu ({arama_suresi:.2f} sn), "
          f"{len(baglam)} karakter baglam\n")

    if sadece_arama:
        for satir in kaynaklar:
            print("  " + satir)
        print()
        for p in parcalar[:3]:
            print("─" * 70)
            print(f"[{p.citation()}]")
            print(textwrap.shorten(p.text.replace("\n", " "), 400, placeholder=" …"))
        kb.close()
        return 0

    # Model ayarlari ayri bir calisma alanindan gelebilir: bilgi tabani
    # bir uc tanimlamak zorunda degil, sizin kurulumunuz zaten tanimliyor.
    model_ayari = load_settings(ayar_alani) if ayar_alani else taban_ayari
    if not model_ayari.llm_ready:
        print(f"HATA: model cagrilamiyor: {model_ayari.llm_hint}", file=sys.stderr)
        print("Ayarlarin bulundugu calisma alanini verin:  --ayar ./demo",
              file=sys.stderr)
        kb.close()
        return 1

    from deerx.llm import build_client

    istemci = build_client(model_ayari)
    basladi = time.time()
    sonuc = istemci.complete(
        role="lead",
        system=SISTEM,
        messages=[{"role": "user",
                   "content": SORU_SABLONU.format(
                       n=len(parcalar), alintilar=baglam, soru=soru)}],
        tools=[],
    )
    model_suresi = time.time() - basladi

    print("═" * 70)
    print(sonuc.text.strip())
    print("═" * 70)
    print("\nKaynaklar:")
    for satir in kaynaklar:
        print("  " + satir)
    print(f"\n{model_ayari.model_lead} · {sonuc.usage.output_tokens} cikti tk "
          f"· {model_suresi:.1f} sn")
    kb.close()
    return 0


def main() -> int:
    a = argparse.ArgumentParser(description="DeerX bilgi tabanina soru sorar.")
    a.add_argument("soru", help="sorulacak soru")
    a.add_argument("--taban", type=Path, default=VARSAYILAN_TABAN)
    a.add_argument("--ayar", type=Path, default=None,
                   help="model ayarlarinin alinacagi calisma alani")
    a.add_argument("-k", type=int, default=8, help="kac parca getirilsin")
    a.add_argument("--sadece-arama", action="store_true",
                   help="modeli cagirma, yalnizca bulunanlari goster")
    a.add_argument("--azami-karakter", type=int, default=60000,
                   help="baglama girecek azami karakter")
    arg = a.parse_args()
    return sor(
        arg.soru,
        taban=arg.taban.resolve(),
        ayar_alani=arg.ayar.resolve() if arg.ayar else None,
        k=arg.k,
        sadece_arama=arg.sadece_arama,
        azami_karakter=arg.azami_karakter,
    )


if __name__ == "__main__":
    raise SystemExit(main())
