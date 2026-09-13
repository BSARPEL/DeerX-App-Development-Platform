"""Kullanicinin GECMISI: baska projelerde ne yapildi, ne konusuldu.

Neden ayri bir modul
--------------------
Her proje kendi SQLite dosyasi (`<yol>/.deerx/deerx.db`). Bir projede
konusurken oteki projelerden haberdar olmanin tek yolu N dosyayi okumak
ve bunu YANLIS yapmanin bedeli olculdu: `AppState.runtime()` uzerinden
acmak her aciliste YAZAR (sema gocu, yetim kosu devralma) ve
`MAX_OPEN_PROJECTS` tahliyesi baskasinin dev sunucusunu, tarayicisini ve
konteynerini kapatir. "Sadece bakiyorum" diye acilan bir proje baskasinin
ortamini sokemez.

Bu yuzden okuma ilkeleri TEK YERDE burada durur ve hem web katmani hem
danisman ayni fonksiyonlari cagirir. Iki ayri kopya olsaydi biri
otekinden sessizce ayrilirdi -- ve ayrilan taraf yazan taraf olurdu.

Uc kural, uctan uca
-------------------
1. **Salt okunur.** `mode=ro` + `PRAGMA query_only=ON`. Ikinci kilit
   bilerek: ileride biri URI'yi sadelestirirse yazma sessizce geri
   gelmesin.
2. **Goc YOK.** `ProjectState` hic kurulmaz. Hic acilmamis eski bir
   veritabaninda bir sutun olmayabilir; cevap "o alan bos", sema
   degistirmek degil.
3. **Kapsam CAGIRANDAN gelir.** Hangi projelerin okunacagina bu modul
   karar vermez; cagiran (web katmani) kullanicinin gorebildigi projeleri
   verir. Buraya bir "butun projeler" kisayolu koymak, yetkiyi iki yerde
   iki kez tanimlamak olurdu.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DATA_DIRNAME, LEGACY_DATA_DIRNAME
from .logging import get_logger

log = get_logger(__name__)

# Ozet ve arama sinirlari. Baglam sonsuz degil: gecmisin TAMAMINI her
# sohbete koymak, asil sorunun uzerine yuz ekran eski konusma yigmak
# olurdu. Ozet dar tutulur, derinlik `search` ile istege bagli gelir.
OZET_PROJE = 8
OZET_KARAR = 5
OZET_AKIS = 3
ARAMA_SINIRI = 12
# Bir sohbet satirinin ozete/aramaya girerken kirpildigi yer.
PARCA_UZUNLUK = 400


def project_db(yol: str | Path) -> Path | None:
    """Projenin veri dosyasi; yoksa None.

    `.praxis` -> `.deerx` yeniden adlandirmasi YALNIZCA `load_settings`
    icinde kosuyor ve o yol ayar YAZAR. Okuma oraya girmez: eski adi da
    okur, tasimaz.
    """
    kok = Path(yol)
    yeni = kok / DATA_DIRNAME / "deerx.db"
    if yeni.is_file():
        return yeni
    eski = kok / LEGACY_DATA_DIRNAME / "praxis.db"
    return eski if eski.is_file() else None


def read_only(db: Path) -> sqlite3.Connection:
    """Proje veritabanini SALT OKUNUR acar.

    `mode=ro` yazmayi yasaklar; `query_only` ikinci kilit. `as_uri()`
    mutlak yol ister ve Windows surucu harfini dogru kodlar. WAL guvenli:
    okuyucu yaziciyi bloklamaz ve yarim islem gormez.
    """
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def table_columns(conn: sqlite3.Connection, tablo: str) -> set[str]:
    """Tablonun sutun adlari; tablo yoksa bos kume.

    Hata YUTULMAZ. Bozuk bir dosyada `PRAGMA` de duser ve onu burada
    yakalamak projeyi "bos ama saglam" gibi gosterirdi -- oysa okunamiyor
    olmasi kullanicinin bilmesi gereken sey. Siniflandirmayi `scan_project`
    yapar.
    """
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({tablo})")}


def scan_project(db: Path, okuyucu: Callable[[sqlite3.Connection], Any]) -> tuple[Any, str]:
    """Tek projeyi acar, `okuyucu`ya verir, kapatir; (sonuc, durum) doner.

    Hicbir hata yaniti dusurmez ve proje LISTEDEN ATILMAZ: sessizce
    atlamak, kullanicinin bildigi bir projeyi yok gostermek ve toplami
    sessizce yanlis yapmak olurdu.
    """
    conn = None
    try:
        conn = read_only(db)
        return okuyucu(conn), "ok"
    except (sqlite3.DatabaseError, OSError):
        return None, "unreadable"
    finally:
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()


# ---------------------------------------------------------------------- #
# Kayit tipleri
# ---------------------------------------------------------------------- #
@dataclass(slots=True)
class GecmisKaydi:
    """Gecmisten tek bir parca: karar, konusma satiri ya da is akisi.

    `kind` arama sonucunu ANLAMLI kiliyor: model "bu bir karar mi yoksa
    laf arasinda soylenmis bir sey mi" ayrimini yapabilmeli. Kararla
    sohbeti ayni agirlikta sunmak, gecmise dayanarak yanlis emin olmaya
    yol acardi.
    """

    # `kind` DEGIL `tur`: bu depoda `kind` cikti/belge turu demek
    # (`kind.report`, `kind.screenshot`) ve ayni sozcugu iki anlamda
    # kullanmak sozlugu de kirletirdi.
    tur: str           # decision | chat | workflow | artifact
    project: str
    title: str
    body: str = ""
    at: float = 0.0
    workflow: str = ""

    def to_line(self) -> str:
        bas = f"[{self.project}] {self.title}".strip()
        govde = _kirp(self.body)
        return f"{bas}\n  {govde}" if govde else bas


@dataclass(slots=True)
class ProjeGecmisi:
    """Tek bir projenin okunabilen ozeti."""

    name: str
    slug: str
    status: str = "ok"          # ok | empty | unreadable
    goal: str = ""
    workflows: int = 0
    chats: int = 0
    last_at: float = 0.0
    kayitlar: list[GecmisKaydi] = field(default_factory=list)


def _kirp(metin: str, sinir: int = PARCA_UZUNLUK) -> str:
    duz = " ".join((metin or "").split())
    return duz if len(duz) <= sinir else duz[: sinir - 1] + "…"


def _kelimeler(metin: str) -> set[str]:
    """Aramanin birimi. Turkce ekler icin kok bulma YOK: yanlis kok,
    bulunmasi gereken bir kaydi sessizce elerdi. Uc harften kisa sozcukler
    atilir ("ve", "bir") -- onlar her kayitta gecer ve siralamayi bozar."""
    return {p for p in re.findall(r"\w+", (metin or "").casefold()) if len(p) > 2}


# ---------------------------------------------------------------------- #
# Okuyucu
# ---------------------------------------------------------------------- #
def _secim(var_olan: set[str], istenen: tuple[str, ...]) -> str:
    """`SELECT` listesi: olmayan sutun NULL olarak secilir.

    Goc kosturmadigimiz icin eski bir veritabaninda bir sutun
    bulunmayabilir. Hepsini istemek `no such column` ile duser ve
    `scan_project` o projeyi "okunamadi" sayardi -- oysa kaydin geri
    kalani okunabilir. Eksik alan bos doner; bu dogru cevap, sema
    degistirmek degil.
    """
    return ", ".join(
        ad if ad in var_olan else f"NULL AS {ad}" for ad in istenen
    )


def _proje_okuyucu(conn: sqlite3.Connection) -> dict[str, Any]:
    """Tek projeden gecmis malzemesi. Sema hosgorulu: eksik tablo bos liste."""
    veri: dict[str, Any] = {
        "goal": "", "workflows": [], "decisions": [], "chats": [],
        "artifacts": [], "last_at": 0.0,
    }

    if table_columns(conn, "project"):
        satir = conn.execute(
            "SELECT value FROM project WHERE key = 'goal'"
        ).fetchone()
        veri["goal"] = (satir["value"] if satir else "") or ""

    akis_sutun = table_columns(conn, "workflows")
    if akis_sutun:
        veri["workflows"] = [
            dict(r) for r in conn.execute(
                "SELECT " + _secim(akis_sutun, ("seq", "title", "goal", "status",
                                                "created_at"))
                + " FROM workflows ORDER BY "
                + ("seq" if "seq" in akis_sutun else "rowid") + " DESC LIMIT 50"
            )
        ]
        for w in veri["workflows"]:
            veri["last_at"] = max(veri["last_at"], float(w.get("created_at") or 0.0))

    karar_sutun = table_columns(conn, "decisions")
    if karar_sutun:
        # Yalnizca VAR OLAN sutunlar secilir. Goc kosturmadigimiz icin hic
        # acilmamis eski bir veritabaninda `rationale` ya da `tradeoffs`
        # bulunmayabilir; hepsini istemek `no such column` ile duser ve
        # `scan_project` o projeyi "okunamadi" sayardi -- oysa karar
        # ORADA duruyor ve okunabilir. Eksik alan bos kalir.
        veri["decisions"] = [
            dict(r) for r in conn.execute(
                "SELECT " + _secim(karar_sutun, ("key", "title", "choice",
                                                 "rationale", "tradeoffs", "created_at"))
                + " FROM decisions ORDER BY "
                + ("created_at" if "created_at" in karar_sutun else "rowid")
                + " DESC LIMIT 100"
            )
        ]

    if table_columns(conn, "workflow_chat"):
        # Is akisinin HEDEFI sohbet satirina yapistirilir: "su cumle hangi
        # isin ortasinda soylendi" bilgisi olmadan eski bir konusma
        # yaniltici olur.
        akis_adi = {}
        if akis_sutun and "id" in akis_sutun:
            akis_adi = {
                r["id"]: (r["title"] or r["goal"] or "")
                for r in conn.execute("SELECT id, title, goal FROM workflows")
            }
        sohbet_sutun = table_columns(conn, "workflow_chat")
        veri["chats"] = [
            {**dict(r), "workflow": akis_adi.get(r["workflow_id"], "")}
            for r in conn.execute(
                "SELECT " + _secim(sohbet_sutun, ("workflow_id", "role", "content", "at"))
                + " FROM workflow_chat ORDER BY "
                + ("at" if "at" in sohbet_sutun else "rowid") + " DESC LIMIT 400"
            )
        ]
        for c in veri["chats"]:
            veri["last_at"] = max(veri["last_at"], float(c.get("at") or 0.0))

    cikti_sutun = table_columns(conn, "artifacts")
    if cikti_sutun:
        veri["artifacts"] = [
            dict(r) for r in conn.execute(
                "SELECT " + _secim(cikti_sutun, ("name", "kind", "summary"))
                + " FROM artifacts ORDER BY "
                + ("created_at" if "created_at" in cikti_sutun else "rowid")
                + " DESC LIMIT 100"
            )
        ]
    return veri


class UserHistory:
    """Kullanicinin gorebildigi projelerin gecmisi; salt okunur.

    Kapsam CAGIRANDAN gelir: `projeler` kullanicinin gordukleridir.
    Yetkiyi burada ikinci kez tanimlamak, iki yerde iki kural demekti.

    Okuma TEMBEL ve bir kez: ilk soruda taranir, sonra bellekte durur.
    Sohbetin her turunda N dosyayi yeniden acmak, uzun bir konusmada
    onlarca gereksiz acilis demekti.
    """

    def __init__(
        self,
        projeler: Iterable[tuple[str, str, str | Path]],
        *,
        simdiki_slug: str = "",
    ) -> None:
        # (ad, slug, yol) uclusu: `Project` nesnesine bagimli olmamak icin.
        # Bu modul `web.projects`i ithal etmiyor ve etmemeli -- CLI'de de
        # kullanilabilsin diye.
        self._projeler = list(projeler)
        self._simdiki = simdiki_slug
        self._okundu = False
        self._gecmis: list[ProjeGecmisi] = []

    # -- okuma ----------------------------------------------------------
    def _oku(self) -> list[ProjeGecmisi]:
        if self._okundu:
            return self._gecmis
        self._okundu = True
        for ad, slug, yol in self._projeler:
            # SIMDIKI proje disarida: onun durumu ve sohbeti danismana
            # zaten tam haliyle gidiyor. Ikinci kez, kirpilmis olarak
            # koymak baglami sisirir ve modele ayni seyi iki farkli
            # ayrintida gosterirdi.
            if slug and slug == self._simdiki:
                continue
            db = project_db(yol)
            if db is None:
                self._gecmis.append(ProjeGecmisi(name=ad, slug=slug, status="empty"))
                continue
            veri, durum = scan_project(db, _proje_okuyucu)
            if durum != "ok" or veri is None:
                self._gecmis.append(
                    ProjeGecmisi(name=ad, slug=slug, status="unreadable")
                )
                continue
            self._gecmis.append(self._kur(ad, slug, veri))
        return self._gecmis

    @staticmethod
    def _kur(ad: str, slug: str, veri: dict[str, Any]) -> ProjeGecmisi:
        kayitlar: list[GecmisKaydi] = []
        for w in veri["workflows"]:
            kayitlar.append(GecmisKaydi(tur="workflow", project=ad,
                title=(w.get("title") or w.get("goal") or "").strip() or f"#{w.get('seq')}",
                body=w.get("goal") or "",
                at=float(w.get("created_at") or 0.0),
            ))
        for d in veri["decisions"]:
            govde = " · ".join(
                x for x in (d.get("choice"), d.get("rationale"), d.get("tradeoffs")) if x
            )
            kayitlar.append(GecmisKaydi(tur="decision", project=ad,
                title=f"{d.get('key', '')} {d.get('title', '')}".strip(),
                body=govde, at=float(d.get("created_at") or 0.0),
            ))
        for c in veri["chats"]:
            kim = "Kullanici" if c.get("role") == "user" else "Danisman"
            kayitlar.append(GecmisKaydi(tur="chat", project=ad, title=kim,
                body=c.get("content") or "", at=float(c.get("at") or 0.0),
                workflow=c.get("workflow") or "",
            ))
        for a in veri["artifacts"]:
            kayitlar.append(GecmisKaydi(tur="artifact", project=ad,
                title=a.get("name") or "", body=a.get("summary") or "",
            ))
        return ProjeGecmisi(
            name=ad, slug=slug, goal=veri["goal"],
            workflows=len(veri["workflows"]),
            chats=len([k for k in kayitlar if k.tur == "chat"]),
            last_at=float(veri["last_at"]),
            kayitlar=kayitlar,
        )

    # -- sunum ----------------------------------------------------------
    def projects(self) -> list[ProjeGecmisi]:
        return list(self._oku())

    def is_empty(self) -> bool:
        return not any(p.kayitlar for p in self._oku())

    def summary(self, *, limit: int = OZET_PROJE) -> str:
        """Her sohbete giren KISA ozet: hangi projeler, ne kararlar.

        Tamami degil bilerek. Gecmisin hepsini her mesaja koymak asil
        sorunun uzerine yuz ekran eski konusma yigmak olurdu; derinlik
        `search_history` araciyla, model istediginde gelir.
        """
        projeler = [p for p in self._oku() if p.status == "ok" and p.kayitlar]
        projeler.sort(key=lambda p: p.last_at, reverse=True)
        if not projeler:
            return ""

        satirlar: list[str] = []
        for p in projeler[:limit]:
            bas = f"### {p.name}"
            if p.goal:
                bas += f" — {_kirp(p.goal, 160)}"
            satirlar.append(bas)

            akislar = [k for k in p.kayitlar if k.tur == "workflow"][:OZET_AKIS]
            if akislar:
                satirlar.append(
                    "- Is akislari: " + "; ".join(_kirp(k.title, 80) for k in akislar)
                )
            kararlar = [k for k in p.kayitlar if k.tur == "decision"][:OZET_KARAR]
            for k in kararlar:
                satirlar.append(f"- {k.title}: {_kirp(k.body, 200)}")
            if p.chats:
                satirlar.append(f"- {p.chats} sohbet satiri (arama ile ulasilabilir)")
            satirlar.append("")
        return "\n".join(satirlar).rstrip()

    def search(self, query: str, *, limit: int = ARAMA_SINIRI, tur: str = "") -> list[GecmisKaydi]:
        """Gecmiste arama: sozcuk ortusmesi, en yakin once.

        Gomme vektoru YOK ve bilerek: bilgi tabaninin gomme katmani proje
        BASINA kurulu ve capraz bir indeks, her projenin vektor deposunu
        acmak (ya da ucuncu bir depo tutup senkron tutmak) demekti. Sozcuk
        ortusmesi burada yeterli, cunku aranan sey genellikle bir ozel ad:
        proje adi, teknoloji, karar anahtari.
        """
        anahtarlar = _kelimeler(query)
        if not anahtarlar:
            return []
        puanli: list[tuple[float, GecmisKaydi]] = []
        for p in self._oku():
            for kayit in p.kayitlar:
                if tur and kayit.tur != tur:
                    continue
                govde = _kelimeler(f"{kayit.title} {kayit.body} {kayit.workflow}")
                ortak = anahtarlar & govde
                if not ortak:
                    continue
                # Karar ve is akisi, laf arasinda gecen bir sohbet
                # satirindan daha agir basar: "gecmiste ne yaptik"
                # sorusunun cevabi once kayda gecmis olandir.
                agirlik = {"decision": 1.6, "workflow": 1.3}.get(kayit.tur, 1.0)
                puanli.append((len(ortak) * agirlik, kayit))
        puanli.sort(key=lambda x: (-x[0], -x[1].at))
        return [k for _p, k in puanli[:limit]]

    def unreadable(self) -> list[str]:
        """Okunamayan projelerin adlari. Sessizce atlanmaz: eksik bir
        gecmisle konusuldugunu kullanici bilmeli."""
        return [p.name for p in self._oku() if p.status == "unreadable"]
