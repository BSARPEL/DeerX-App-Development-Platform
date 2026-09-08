"""Proje kaydi: hangi dizin kimin projesi, kim hangi rolle uye.

Bir PROJE, kayitli bir DIZINDIR. Kimligi platform veritabaninda bir
satirdir ama verisi kendi dosyasinda kalir: `<proje>/.deerx/deerx.db`.
Yol, projenin kendisi degil, satirin bir ozelligidir.

Alternatif -- her seyi tek merkezi veritabaninda toplayip butun tablolara
`project_id` eklemek -- bilerek reddedildi. Uc olculmus sebep:

1. `requirements.key`, `tasks.key`, `artifacts.name`, `documents.source`
   ve `phase_state(phase)` uzerindeki UNIQUE kisitlari `(project_id, ...)`
   bilesigine cikarmak gerekirdi. SQLite'ta bu, bes tabloyu yeniden
   yaratip veri kopyalamak demek: deponun ilk kez VERI TASIYAN, yarida
   kesilirse projeyi bozan gocu.
2. Test fikstur katmani tek `ProjectState` / tek `KnowledgeBase`
   varsayimini tasiyor. Dizin modelinde `ProjectState(db_path)` imzasi
   hic degismiyor ve yuzlerce test oldugu gibi ayakta kaliyor.
3. Ajanin gelistirme ortami ZATEN bir dizin: kabin `/workspace`e
   bagliyor, servisler orada kosuyor, dosya araclari oraya hapsolmus.
   Veriyi tek dosyada birlestirmek, ortami yine de dizin bazinda ayirmak
   zorunda birakirdi -- iki farkli izolasyon ekseni.

Yetki IKI KATMANLIDIR ve karistirilmamali:
  * hesap rolu (`admin` / `user`) PLATFORM islemleri icin -- kimlik
    bilgileri, yalitim, kim hesap acabilir;
  * proje rolu (`owner` / `developer` / `viewer`) o projedeki isler icin.
Bir platform yoneticisi her projeye erisir; bir proje sahibi platform
ayarlarina dokunamaz.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..i18n import t
from ..logging import get_logger

log = get_logger("web.projects")

# Kucukten buyuge. Sira ONEMLI: yetki kontrolu "en az su rol" diye
# soruluyor ve karsilastirma bu indeks uzerinden yapiliyor.
PROJECT_ROLES = ("viewer", "developer", "owner")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY,
    -- URL'de ve arayuzde gorunen kisa ad.
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    -- Dizin. UNIQUE, cunku iki kayit ayni dizini gosterirse iki proje
    -- ayni veritabanini paylasir ve biri otekinin gorevlerini gorur.
    path       TEXT NOT NULL UNIQUE,
    owner_id   INTEGER,
    created_at REAL NOT NULL,
    -- Arsivlenen proje SILINMEZ: dizin diskte durur ve kayit geri
    -- alinabilir. Bir projeyi silmek, o projede yapilmis her seyin
    -- gecmisini de silmek olurdu.
    archived   INTEGER NOT NULL DEFAULT 0,
    -- Bu projenin konteynerine ayrilan port dilimi. Docker yayinlanan
    -- portlari konteyner YARATILIRKEN ayirir ve sonradan ekleyemez, yani
    -- dilim kap kurulmadan ONCE ve kalici olarak belli olmali. Proje
    -- silinene kadar degismez: bir ajanin "uygulaman 8103'te" diye
    -- verdigi adres, sunucu yeniden baslayinca baska bir projeye
    -- gitmemeli.
    port_base  INTEGER NOT NULL DEFAULT 0,
    port_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL DEFAULT 'developer',
    added_at   REAL NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS members_by_user ON project_members(user_id);
"""


class ProjectError(Exception):
    """Proje kaydiyla ilgili, kullaniciya gosterilebilir hata."""


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    slug: str
    name: str
    path: Path
    owner_id: int | None
    created_at: float
    archived: bool
    port_base: int = 0
    port_count: int = 0
    # Cagiran kullanicinin bu projedeki rolu; listeleme sirasinda doldurulur.
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "path": str(self.path),
            "owner_id": self.owner_id,
            "created_at": self.created_at,
            "archived": self.archived,
            "port_base": self.port_base,
            "port_count": self.port_count,
            "role": self.role,
        }


# Turkce harfler ASCII karsiliklarina KUCULTMEDEN ONCE cevrilir.
# Python'un `str.lower()`i "İ" icin "i" + birlesen nokta uretiyor ve o
# nokta `[a-z0-9]` disinda kaldigi icin bir ayraca donusuyordu:
# "İş Akışı" -> "i-s-akisi". Olculdu.
_ASCIILESTIR = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


def slugify(text: str) -> str:
    """Dizin adindan URL'de kullanilabilir bir kisa ad uretir."""
    ascii_ = text.strip().translate(_ASCIILESTIR).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")
    return slug or "proje"


def role_at_least(role: str, needed: str) -> bool:
    """`role`, `needed` kadar ya da daha yetkili mi."""
    if role not in PROJECT_ROLES or needed not in PROJECT_ROLES:
        return False
    return PROJECT_ROLES.index(role) >= PROJECT_ROLES.index(needed)


class ProjectStore:
    """Projeler ve uyelikler. Platform veritabanini paylasir."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Baglanti is parcacigi basina; gerekcesi `ProjectState` ile ayni.
        self._yerel = threading.local()
        self._baglanti_kilidi = threading.Lock()
        self._baglantilar: list[sqlite3.Connection] = []
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Var olan bir kaydi yeni sutunlarla yukseltir.

        `CREATE TABLE IF NOT EXISTS` var olan tabloyu degistirmez.
        """
        mevcut = {r["name"] for r in self._conn.execute("PRAGMA table_info(projects)")}
        for ad, tanim in (
            ("port_base", "INTEGER NOT NULL DEFAULT 0"),
            ("port_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if ad not in mevcut:
                self._conn.execute(f"ALTER TABLE projects ADD COLUMN {ad} {tanim}")

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._yerel, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._yerel.conn = conn
            with self._baglanti_kilidi:
                self._baglantilar.append(conn)
        return conn

    @contextmanager
    def _islem(self) -> Iterator[None]:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def close(self) -> None:
        with self._baglanti_kilidi:
            for conn in self._baglantilar:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover - kapanista onemsiz
                    pass
            self._baglantilar.clear()
        self._yerel = threading.local()

    # ------------------------------------------------------------------ #
    # Okuma
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row(row: sqlite3.Row, role: str = "") -> Project:
        return Project(
            id=int(row["id"]),
            slug=row["slug"],
            name=row["name"],
            path=Path(row["path"]),
            owner_id=row["owner_id"],
            created_at=float(row["created_at"]),
            archived=bool(row["archived"]),
            port_base=int(row["port_base"] or 0),
            port_count=int(row["port_count"] or 0),
            role=role or (row["role"] if "role" in row.keys() else ""),
        )

    def get(self, project_id: int) -> Project | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return self._row(row) if row else None

    def by_path(self, path: Path) -> Project | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE path = ?", (str(Path(path).resolve()),)
        ).fetchone()
        return self._row(row) if row else None

    def all_projects(self, *, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY name COLLATE NOCASE"
        return [self._row(r) for r in self._conn.execute(sql)]

    def for_user(
        self, user_id: int, *, is_admin: bool = False, include_archived: bool = False
    ) -> list[Project]:
        """Kullanicinin gorebilecegi projeler.

        Platform yoneticisi HEPSINI gorur -- yoksa sahibi ayrilmis bir
        proje kimsenin ulasamadigi bir dizine donusurdu. Rol sutunu
        yoneticide uyelik yoksa `owner` olarak doldurulur: gordugu her
        seye mudahale edebilmesi gerekiyor.
        """
        if is_admin:
            projeler = self.all_projects(include_archived=include_archived)
            uyelik = {
                int(r["project_id"]): r["role"]
                for r in self._conn.execute(
                    "SELECT project_id, role FROM project_members WHERE user_id = ?",
                    (user_id,),
                )
            }
            return [
                Project(
                    id=p.id, slug=p.slug, name=p.name, path=p.path,
                    owner_id=p.owner_id, created_at=p.created_at,
                    archived=p.archived, port_base=p.port_base,
                    port_count=p.port_count, role=uyelik.get(p.id, "owner"),
                )
                for p in projeler
            ]

        sql = (
            "SELECT p.*, m.role AS role FROM projects p "
            "JOIN project_members m ON m.project_id = p.id "
            "WHERE m.user_id = ?"
        )
        if not include_archived:
            sql += " AND p.archived = 0"
        sql += " ORDER BY p.name COLLATE NOCASE"
        return [self._row(r, r["role"]) for r in self._conn.execute(sql, (user_id,))]

    def role_of(self, project_id: int, user_id: int) -> str:
        row = self._conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        return row["role"] if row else ""

    def members(self, project_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT user_id, role, added_at FROM project_members "
            "WHERE project_id = ? ORDER BY added_at",
            (project_id,),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Yazma
    # ------------------------------------------------------------------ #
    def _free_slug(self, taban: str) -> str:
        slug = slugify(taban)
        n = 1
        while self._conn.execute(
            "SELECT 1 FROM projects WHERE slug = ?", (slug,)
        ).fetchone():
            n += 1
            slug = f"{slugify(taban)}-{n}"
        return slug

    def _bos_dilim(self, taban: int, adet: int) -> int:
        """Kullanilmayan ilk port dilimini bulur.

        Dilimler bitisiktir ve ARALARINDAKI BOSLUK YENIDEN KULLANILIR:
        proje silindiginde dilimi bosalir ve bir sonraki proje onu alir.
        Yoksa uzun omurlu bir kurulumda taban surekli yukselir ve bir gun
        ayricalikli olmayan port araligini asardi.
        """
        alinan = {
            int(r["port_base"])
            for r in self._conn.execute(
                "SELECT port_base FROM projects WHERE port_base > 0"
            )
        }
        aday = taban
        while aday in alinan:
            aday += adet
        return aday

    def create(
        self,
        path: Path,
        *,
        name: str = "",
        owner_id: int | None = None,
        members: list[tuple[int, str]] | None = None,
        port_base: int = 8100,
        port_count: int = 10,
    ) -> Project:
        """Bir dizini proje olarak kaydeder.

        Dizin OLUSTURULMAZ ve icerigine dokunulmaz: kayit, var olan bir
        seyin uzerine konan bir etikettir. Ayni dizin ikinci kez
        kaydedilemez.
        """
        cozulen = Path(path).expanduser().resolve()
        varolan = self.by_path(cozulen)
        if varolan is not None:
            raise ProjectError(t("project.path_taken", path=cozulen))

        etiket = (name or cozulen.name).strip() or cozulen.name
        with self._islem():
            dilim = self._bos_dilim(port_base, port_count)
            cur = self._conn.execute(
                "INSERT INTO projects "
                "(slug, name, path, owner_id, created_at, archived, "
                " port_base, port_count) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (self._free_slug(etiket), etiket, str(cozulen), owner_id,
                 time.time(), dilim, port_count),
            )
            pid = int(cur.lastrowid or 0)
            hepsi = list(members or [])
            if owner_id is not None and all(uid != owner_id for uid, _ in hepsi):
                hepsi.append((owner_id, "owner"))
            for uid, rol in hepsi:
                self._conn.execute(
                    "INSERT OR REPLACE INTO project_members "
                    "(project_id, user_id, role, added_at) VALUES (?, ?, ?, ?)",
                    (pid, uid, rol if rol in PROJECT_ROLES else "developer", time.time()),
                )
        proje = self.get(pid)
        assert proje is not None
        log.info(t("project.created", name=proje.name, path=proje.path))
        return proje

    def rename(self, project_id: int, name: str) -> Project:
        etiket = name.strip()
        if not etiket:
            raise ProjectError(t("project.name_required"))
        self._conn.execute(
            "UPDATE projects SET name = ? WHERE id = ?", (etiket, project_id)
        )
        self._conn.commit()
        proje = self.get(project_id)
        if proje is None:
            raise ProjectError(t("project.unknown", id=project_id))
        return proje

    def set_archived(self, project_id: int, archived: bool) -> Project:
        self._conn.execute(
            "UPDATE projects SET archived = ? WHERE id = ?",
            (1 if archived else 0, project_id),
        )
        self._conn.commit()
        proje = self.get(project_id)
        if proje is None:
            raise ProjectError(t("project.unknown", id=project_id))
        return proje

    def set_member(self, project_id: int, user_id: int, role: str) -> None:
        if role not in PROJECT_ROLES:
            raise ProjectError(t("project.bad_role", role=role))
        self._conn.execute(
            "INSERT OR REPLACE INTO project_members "
            "(project_id, user_id, role, added_at) VALUES (?, ?, ?, "
            " COALESCE((SELECT added_at FROM project_members "
            "           WHERE project_id = ? AND user_id = ?), ?))",
            (project_id, user_id, role, project_id, user_id, time.time()),
        )
        self._conn.commit()

    def remove_member(self, project_id: int, user_id: int) -> bool:
        """Uyeligi kaldirir. SON SAHIBI kaldirmaz.

        Sahipsiz bir proje, yalnizca platform yoneticisinin ulasabildigi
        bir dizine donusur; bunu kaza eseri yapmak kolay olmamali.
        """
        proje = self.get(project_id)
        if proje is None:
            return False
        sahipler = [
            m for m in self.members(project_id) if m["role"] == "owner"
        ]
        if len(sahipler) <= 1 and any(m["user_id"] == user_id for m in sahipler):
            raise ProjectError(t("project.last_owner"))
        cur = self._conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def forget_user(self, user_id: int) -> int:
        """Silinen bir hesabin uyeliklerini temizler."""
        cur = self._conn.execute(
            "DELETE FROM project_members WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()
        return cur.rowcount
