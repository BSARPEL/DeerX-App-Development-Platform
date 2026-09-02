"""Depoya konacak ekran goruntulerini ceker.

Gercek arayuz, gercek verilerle -- ama KULLANICININ verisiyle degil:
`demo_workspace.py` bu is icin gosterilebilir bir calisma alani kurar. Depo
herkese acik; kullanicinin proje icerigi ve Windows kullanici adi oraya
girmemeli.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_workspace import DEMO_PAROLA  # noqa: E402

CIKTI = Path(__file__).resolve().parents[2] / "docs" / "images"
GENISLIK, YUKSEKLIK = 1440, 900


def hazirla(page, adres: str, dil: str) -> None:
    page.goto(adres, wait_until="networkidle")
    page.wait_for_timeout(1200)
    # Demo calisma alaninin kullanicilari var (denetim gunlugu icin), yani
    # giris kapisi aciliyor. Parola `demo_workspace.py` icinde yaziyor ve
    # her kurulumda ayni: bu calisma alani her seferinde sifirdan kuruluyor
    # ve tek isi fotograflanmak.
    if page.evaluate("() => !document.querySelector('#auth-gate').hidden"):
        page.fill("#login-username", "deniz")
        page.fill("#login-password", DEMO_PAROLA)
        page.click("#login-submit")
        page.wait_for_timeout(1800)
        assert page.evaluate("() => document.querySelector('#auth-gate').hidden"), (
            "giris yapilamadi; demo calisma alani yeniden kurulmali"
        )
    # Dil sunucuda tutulur; segmenti tikla ve otursun.
    page.evaluate(
        "(d) => document.querySelector(`.lang-opt[data-lang='${d}']`).click()", dil
    )
    page.wait_for_timeout(900)
    # Tema: koyu. Dugmeye korlemesine basmak yerine degeri DOGRUDAN yaz --
    # `data-theme` bos gelebiliyor (sistem tercihine dusuyor) ve tek tik
    # o durumda ters yone goturuyordu.
    page.evaluate("""() => {
      document.documentElement.dataset.theme = 'dark';
      localStorage.setItem('deerx-theme', 'dark');
    }""")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"

    # Sol rayda calisma alani yaziyor ve ray HER karede gorunur. Yol artik
    # yalnizca ipucunda -- ama denetim rayin METNINE bakar, bir elemanin
    # adina degil: yol ileride tekrar yazilirsa da yakalasin.
    ray = page.evaluate("() => document.querySelector('.rail').textContent")
    ad = page.evaluate("() => document.querySelector('#rail-ws-name').textContent")
    # Bos bir tarama sessizce GECERDI. Once taramanin gercekten rayin
    # dibini gordugunu dogrula.
    assert ad and ad in ray, "ray dibi taranamadi; denetim bosa donerdi"
    for parca in ("Users", "home/", "Documents"):
        assert parca not in ray, (
            f"kadrajdaki rayda ev dizini yaziyor: {ray[-120:]!r}\n"
            "Notr bir calisma alani yolu kullanin (C:\\deerx-demo, /tmp/deerx-demo)."
        )


def gorunum(page, ad: str) -> None:
    page.evaluate(
        "(v) => document.querySelector(`.rail-item[data-view='${v}']`).click()", ad
    )
    page.wait_for_timeout(1400)


def cek(page, ad: str, dil: str) -> None:
    CIKTI.mkdir(parents=True, exist_ok=True)
    yol = CIKTI / f"{ad}-{dil}.png"
    page.screenshot(path=str(yol))
    print(f"  {yol.name}  {yol.stat().st_size // 1024} KB")


def tur(page, adres: str, dil: str) -> None:
    hazirla(page, adres, dil)

    # 1) Genel bakis -- boru hatti durmus, cevap bekleyen sorularla.
    gorunum(page, "overview")
    page.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(400)
    cek(page, "overview", dil)

    # 2) Gelistirme
    gorunum(page, "develop")
    cek(page, "develop", dil)

    # 3) Is akisi -> ilk akisin kosulari
    gorunum(page, "workflow")
    page.evaluate("""() => {
      const r = document.querySelector('.run-row[data-workflow]');
      if (r) r.click();
    }""")
    page.wait_for_timeout(1600)
    cek(page, "workflow", dil)

    # 4) Analiz
    gorunum(page, "analysis")
    cek(page, "analysis", dil)

    # 5) Plan
    gorunum(page, "plan")
    cek(page, "plan", dil)

    # 6) Ciktilar -- mockup cerceve icinde canli.
    gorunum(page, "artifacts")
    page.evaluate("""() => {
      const b = document.querySelector('[data-artifact="dispatch-board.html"]');
      if (b) b.click();
    }""")
    page.wait_for_timeout(2000)
    cek(page, "artifacts", dil)

    # 7) Canli akis
    gorunum(page, "stream")
    page.wait_for_timeout(800)
    cek(page, "stream", dil)

    # 8) Ayarlar -- yalitim paneli acik.
    gorunum(page, "settings")
    page.evaluate("""() => {
      const s = document.querySelector('#set-execution');
      s.value = 'docker';
      s.dispatchEvent(new Event('change'));
      // Kaydiran kap PENCERE degil `.content`; `scrollTo(0, ...)` bu
      // yuzden hicbir sey yapmiyordu. `scrollIntoView` dogru kabi bulur,
      // sonra panelin BASLIGI da kadraja girsin diye biraz geri alinir.
      const panel = document.querySelector('#sandbox-fields').closest('.panel');
      panel.scrollIntoView({block: 'start'});
      let kap = panel.parentElement;
      while (kap && kap.scrollHeight <= kap.clientHeight) kap = kap.parentElement;
      if (kap) kap.scrollTop = Math.max(0, kap.scrollTop - 150);
    }""")
    page.wait_for_timeout(700)
    gorunur = page.evaluate("""() => {
      const icinde = (el) => {
        const r = el.getBoundingClientRect();
        return r.bottom > 0 && r.top < innerHeight;
      };
      return [...document.querySelectorAll('#env-table td')]
        .filter(icinde).map(e => e.textContent).join(' ');
    }""")
    assert "Users" not in gorunur, f"kadrajda kullanici yolu var: {gorunur[:80]}"
    cek(page, "settings", dil)

    # 9) Denetim gunlugu -- kim ne zaman girmis, ne calistirmis.
    page.evaluate("""() => {
      // Sabit bir pay yerine OLCULMUS bir hedef: panelin ust kenari her
      // dilde ayni yere otursun. Payla denemek bir dilde cerceveyi
      // kirpiyor, otekinde bir onceki panelin dibini kadraja sokuyordu.
      const panel = document.querySelector('#audit-panel');
      panel.scrollIntoView({block: 'start'});
      let kap = panel.parentElement;
      while (kap && kap.scrollHeight <= kap.clientHeight) kap = kap.parentElement;
      if (kap) kap.scrollTop += panel.getBoundingClientRect().top - 152;
    }""")
    page.wait_for_timeout(700)
    satir = page.evaluate(
        "() => document.querySelectorAll('#audit-table tbody tr').length"
    )
    assert satir >= 10, f"denetim gunlugu bos gorunuyor ({satir} satir)"
    cek(page, "audit", dil)


def main() -> None:
    with sync_playwright() as pw:
        tarayici = pw.chromium.launch()
        sayfa = tarayici.new_page(
            viewport={"width": GENISLIK, "height": YUKSEKLIK},
            device_scale_factor=1,
            color_scheme="dark",
        )
        print("Ingilizce:")
        tur(sayfa, "http://127.0.0.1:8781/", "en")
        print("Turkce:")
        tur(sayfa, "http://127.0.0.1:8782/", "tr")
        tarayici.close()


if __name__ == "__main__":
    main()
