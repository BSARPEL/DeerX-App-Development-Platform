"""Kullanici hesaplari ve oturum yonetimi.

Bu sunucu dosya yazabilir ve kabuk komutu calistirabilir. Kimlik dogrulama
onu ag uzerinde acilabilir kilan seydir; bu yuzden birkac karar bilerek
sikidir:

Parolalar `scrypt` ile saklanir. Bellek-zor bir turetme fonksiyonu ve
standart kutuphanede var — yeni bir bagimlilik getirmeden bcrypt/argon2
sinifi bir koruma saglar. Her kullanicinin kendi tuzu vardir.

Oturumlar sunucu tarafinda tutulur. Imzali bir cerez daha az kod olurdu ama
iptal edilemezdi: parolasi calinan bir hesabin acik oturumlarini aninda
kapatmak gerekir.

Kullanici sayimi bilgi sizdirir. Bilinmeyen bir kullanici adinda da KDF
calistirilir; aksi halde yanit suresi "bu kullanici var mi" sorusunu
cevaplardi.

Ilk yonetici bir kurulum jetonuyla olusturulur. Jeton yalnizca sunucunun
konsoluna basilir: sunucuya once ulasan biri yonetici hesabini kapamasin.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from hashlib import scrypt
from pathlib import Path
from typing import Any

from ..i18n import t
from ..logging import get_logger

log = get_logger("auth")

# scrypt parametreleri. n=2**14 masaustu bir makinede ~100 ms surer: kullanici
# fark etmez, kaba kuvvet denemesi icin pahalidir.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

SESSION_COOKIE = "deerx_session"
# Mutlak omur: bu surenin sonunda oturum her kosulda biter.
SESSION_MAX_AGE = 7 * 24 * 3600
# Bosta kalma suresi: kullanilmayan bir oturum bu kadar sonra duser.
SESSION_IDLE = 24 * 3600

# Kaba kuvvete karsi: ayni kullanici adinda ust uste basarisiz denemeler.
MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 300

# NIST SP 800-63B: kullanicinin sectigi parolalar icin asgari 8 karakter.
# Ayni rehber, karmasiklik kurallarina ve keyfi uzunluk sismesine karsi
# uyarir; onun yerine bilinen parola listeleriyle karsilastirmayi onerir.
MIN_PASSWORD = 8

# Her tarama botunun ilk denedigi parolalar. Reddetmek yerine UYARILIR:
# kendi makinesinde kendi hesabini kuran bir yoneticiye "hayir" demek
# paternalist olur, ama riski gizlemek de dogru degil.
COMMON_PASSWORDS = frozenset({
    "admin", "admin123", "administrator", "password", "password1", "password123",
    "12345678", "123456789", "1234567890", "qwerty", "qwerty123", "letmein",
    "welcome", "welcome1", "changeme", "deerx", "deerx123", "test1234",
    "iloveyou", "abc12345", "passw0rd", "root", "toor", "secret",
})
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

ROLES = ("admin", "user")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    username     TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'user',
    salt         BLOB NOT NULL,
    password     BLOB NOT NULL,
    created_at   REAL NOT NULL,
    last_login    REAL,
    -- Ilk yonetici silinemez, rolu dusurulemez ve kapatilamaz; aksi halde
    -- sisteme girilemez hale getirilebilir.
    is_master    INTEGER NOT NULL DEFAULT 0,
    -- Kapali hesap silinmez, yalnizca giremez. Ayrilan biri geri
    -- donebilir; hesabi silmek gecmisteki izlerini de anlamsizlastirirdi.
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    seen_at    REAL NOT NULL,
    agent      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);

-- Kim, ne zaman, ne yapti. `users` ve `sessions` "su an kim var" sorusunu
-- cevaplar; bu tablo "ne oldu" sorusunu cevaplar ve geriye donuk okunur.
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY,
    at          REAL NOT NULL,
    -- Kullanici silindiginde NULL olur ama `username` metin olarak KALIR.
    -- Iz, isaret ettigi hesaptan daha uzun yasamali: silinen bir hesabin
    -- gecmisi de silinseydi, hesabi silmek gecmisi temizlemenin yolu
    -- olurdu. Basarisiz girislerde zaten hicbir hesaba baglanmaz.
    user_id     INTEGER,
    username    TEXT NOT NULL DEFAULT '',
    -- Sabit bir tanimlayici ('login', 'run.start'). Arayuz cevirir; metin
    -- saklamak, dil degistiginde eski satirlari eski dilde birakirdi.
    action      TEXT NOT NULL,
    -- Islemin dokundugu sey: kosunun basligi, hedef kullanici adi, degisen
    -- ayarlarin listesi. `detail` YAZILMIS metindir; `detail_key` +
    -- `detail_args` onun cevrilebilir hali (kosu basliklarindaki ile ayni
    -- duzen). Arayuz once anahtara bakar, yoksa metne duser.
    detail      TEXT NOT NULL DEFAULT '',
    detail_key  TEXT NOT NULL DEFAULT '',
    detail_args TEXT NOT NULL DEFAULT '{}',
    ip          TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL DEFAULT '',
    -- Islem BASARISIZ da olsa yazilir: reddedilen girisler bir guvenlik
    -- gunlugunun en cok ise yarayan satirlaridir.
    ok          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS audit_by_time ON audit(id DESC);
"""

# Gunluk sinirsiz buyuyemez: proje veritabaniyla ayni dosyayi paylasiyor.
# Son bu kadar satir tutulur.
AUDIT_KEEP = 5000
# Her yazmada budamak, 5000 satirlik bir taramayi her girise yayardi.
# Kimlikler artan oldugu icin bu kosul her bu kadar kayitta bir tutar --
# yani satir sayisi araliksal olarak sinirin bir budama araligi kadar
# uzerine cikabilir. Kesin bir tavan degil, buyumeyi durduran bir tavan.
AUDIT_TRIM_EVERY = 256


class AuthError(Exception):
    """Kimlik dogrulama reddi. Mesaji kullaniciya gosterilebilir."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    is_master: bool
    is_active: bool
    created_at: float
    last_login: float | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "is_master": self.is_master,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_login": self.last_login,
        }


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
    )
    return salt, digest


def check_password_policy(password: str) -> str | None:
    """Kabul edilemez parolalari reddeder; zayif olanlari bildirir.

    Karmasiklik zorunlulugu konmadi: buyuk harf/rakam/simge dayatmak
    kullaniciyi "Parola1!" gibi tahmin edilebilir kaliplara iter ve guvenlik
    kazandirmaz.

    Returns:
        Parola kabul edilebilir ama zayifsa uyari metni; degilse None.

    Raises:
        AuthError: Parola hic kabul edilemez.
    """
    if len(password) < MIN_PASSWORD:
        raise AuthError(t("auth.password_too_short", min=MIN_PASSWORD))
    if password.strip() != password:
        raise AuthError(t("auth.password_spaces"))

    if password.lower() in COMMON_PASSWORDS:
        return t("auth.password_common")
    if len(password) < 12:
        return t("auth.password_short_warning")
    return None


class AuthStore:
    """Kullanicilar ve oturumlar. Proje veritabaniyla ayni dosyayi paylasir."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        # Kaba kuvvet sayaci bellektedir: sunucu yeniden baslayinca sifirlanir.
        # Kalicilastirmak saldirgana veritabani uzerinden hizmet reddi imkani
        # verirdi (baskasinin hesabini kilitlemek icin yanlis parola denemek).
        self._attempts: dict[str, list[float]] = {}
        # Ilk yonetici icin tek kullanimlik kurulum jetonu.
        self._setup_token: str | None = None
        # Son parola isleminin uyarisi; cagiran kullaniciya gosterir.
        self.last_warning: str | None = None

    def _migrate(self) -> None:
        """Once olusturulmus tablolara sonradan eklenen sutunlari ekler.

        `CREATE TABLE IF NOT EXISTS` var olan bir tabloyu degistirmez; bu
        kontrol olmadan mevcut kurulumlar acilista cokerdi.
        """
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(users)")
        }
        if "is_active" not in existing:
            log.info(t("auth.migration_is_active"))
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ #
    # Kurulum
    # ------------------------------------------------------------------ #
    @property
    def is_configured(self) -> bool:
        """En az bir kullanici var mi? Yoksa kimlik dogrulama kapalidir."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) > 0

    def issue_setup_token(self) -> str:
        """Ilk yonetici icin tek kullanimlik jeton uretir.

        Jeton yalnizca sunucunun konsoluna basilir. Aksi halde sunucuya once
        ulasan biri yonetici hesabini kapabilirdi.
        """
        self._setup_token = secrets.token_urlsafe(24)
        return self._setup_token

    def create_first_admin(
        self, token: str, username: str, password: str, display_name: str = ""
    ) -> User:
        if self.is_configured:
            raise AuthError(t("auth.already_configured"))
        if not self._setup_token or not hmac.compare_digest(token, self._setup_token):
            raise AuthError(t("auth.bad_setup_token"))
        user = self._insert(username, password, role="admin",
                            display_name=display_name, is_master=True)
        self._setup_token = None  # tek kullanimlik
        log.info(t("auth.first_admin", name=user.username))
        return user

    # ------------------------------------------------------------------ #
    # Kullanicilar
    # ------------------------------------------------------------------ #
    def _insert(
        self, username: str, password: str, *, role: str,
        display_name: str = "", is_master: bool = False,
    ) -> User:
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise AuthError(t("auth.bad_username"))
        if role not in ROLES:
            raise AuthError(t("auth.bad_role", role=role))
        self.last_warning = check_password_policy(password)
        salt, digest = hash_password(password)
        try:
            cursor = self._conn.execute(
                "INSERT INTO users "
                "(username, display_name, role, salt, password, created_at, is_master) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, display_name.strip(), role, salt, digest,
                 time.time(), int(is_master)),
            )
        except sqlite3.IntegrityError:
            raise AuthError(f"'{username}' zaten kayitli.") from None
        self._conn.commit()
        found = self.get_user(int(cursor.lastrowid))
        assert found is not None
        return found

    def create_user(
        self, username: str, password: str, *, role: str = "user", display_name: str = ""
    ) -> User:
        return self._insert(username, password, role=role, display_name=display_name)

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"], username=row["username"], display_name=row["display_name"],
            role=row["role"], is_master=bool(row["is_master"]),
            is_active=bool(row["is_active"]),
            created_at=row["created_at"], last_login=row["last_login"],
        )

    def get_user(self, user_id: int) -> User | None:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def find(self, username: str) -> User | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._row_to_user(r) for r in rows]

    def set_password(self, user_id: int, password: str) -> str | None:
        warning = check_password_policy(password)
        salt, digest = hash_password(password)
        self._conn.execute(
            "UPDATE users SET salt = ?, password = ? WHERE id = ?", (salt, digest, user_id)
        )
        # Parola degisince o kullanicinin tum oturumlari duser: parolayi
        # degistirmenin amaci zaten acik oturumlari kesmektir.
        self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return warning

    def set_role(self, user_id: int, role: str) -> User:
        if role not in ROLES:
            raise AuthError(t("auth.bad_role", role=role))
        user = self.get_user(user_id)
        if user is None:
            raise AuthError(t("auth.user_not_found"))
        if user.is_master and role != "admin":
            raise AuthError(t("auth.master_role"))
        self._conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        self._conn.commit()
        updated = self.get_user(user_id)
        assert updated is not None
        return updated

    def set_active(self, user_id: int, active: bool) -> User:
        """Hesabi acar ya da kapatir.

        Kapatmak silmek degildir: ayrilan biri geri donebilir ve hesabi
        silmek gecmisteki izlerini de anlamsizlastirirdi. Kapatilan hesabin
        acik oturumlari aninda dusurulur -- yoksa kapatma islemi, kullanici
        cikis yapana kadar hicbir sey yapmamis olurdu.
        """
        user = self.get_user(user_id)
        if user is None:
            raise AuthError(t("auth.user_not_found"))
        if user.is_master and not active:
            raise AuthError(t("auth.master_disable"))

        self._conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?", (int(active), user_id)
        )
        if not active:
            self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            # Kilit sayaci da temizlenir: kapali kalmis bir hesap geri
            # acildiginda eski basarisiz denemeler yuzunden kilitli olmasin.
            self._attempts.pop(user.username, None)
        self._conn.commit()
        updated = self.get_user(user_id)
        assert updated is not None
        return updated

    def delete_user(self, user_id: int) -> None:
        user = self.get_user(user_id)
        if user is None:
            raise AuthError(t("auth.user_not_found"))
        if user.is_master:
            raise AuthError(t("auth.master_delete"))
        self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        # Denetim satirlarinin BAGI kopar, kendileri kalir: `username` metin
        # olarak durur, `user_id` artik var olmayan bir satiri gostermesin.
        # Kimlikler yeniden kullanilmaz ama bos kalan bir kimlik yanlis bir
        # hesaba baglanmis gibi okunurdu.
        self._conn.execute(
            "UPDATE audit SET user_id = NULL WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Giris
    # ------------------------------------------------------------------ #
    def _locked_for(self, username: str) -> float:
        """Kilit bitene kadar kalan saniye; kilitli degilse 0."""
        now = time.time()
        recent = [t for t in self._attempts.get(username, []) if now - t < LOCKOUT_SECONDS]
        self._attempts[username] = recent
        if len(recent) < MAX_ATTEMPTS:
            return 0.0
        return LOCKOUT_SECONDS - (now - recent[-MAX_ATTEMPTS])

    def authenticate(self, username: str, password: str) -> User:
        username = username.strip().lower()
        remaining = self._locked_for(username)
        if remaining > 0:
            raise AuthError(t("auth.locked_out", seconds=int(remaining) + 1))

        user = self.find(username)
        row = self._conn.execute(
            "SELECT salt, password FROM users WHERE username = ?", (username,)
        ).fetchone()

        # Kullanici yoksa da KDF calistirilir: aksi halde yanit suresi
        # "bu kullanici var mi" sorusunu cevaplardi.
        salt = row["salt"] if row else secrets.token_bytes(16)
        expected = row["password"] if row else secrets.token_bytes(SCRYPT_DKLEN)
        _, digest = hash_password(password, salt)

        if user is None or not hmac.compare_digest(digest, expected):
            self._attempts.setdefault(username, []).append(time.time())
            raise AuthError(t("auth.bad_credentials"))

        # Parola dogru ama hesap kapaliysa da reddedilir. Kontrol parola
        # dogrulamasindan SONRA yapilir: once yapilsaydi, yanlis parolayla
        # bile "bu hesap kapali" bilgisi sizardi.
        if not user.is_active:
            raise AuthError(t("auth.account_disabled"))

        self._attempts.pop(username, None)
        self._conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user.id)
        )
        self._conn.commit()
        return user

    # ------------------------------------------------------------------ #
    # Oturumlar
    # ------------------------------------------------------------------ #
    def open_session(self, user: User, agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, seen_at, agent) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, user.id, now, now, agent[:200]),
        )
        self._conn.commit()
        return token

    def resolve_session(self, token: str | None) -> User | None:
        """Cerezi kullaniciya cevirir; suresi dolmus oturumu temizler."""
        if not token:
            return None
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None

        now = time.time()
        if now - row["created_at"] > SESSION_MAX_AGE or now - row["seen_at"] > SESSION_IDLE:
            self.close_session(token)
            return None

        # Bosta kalma suresi her istekte tazelenir; mutlak omur tazelenmez.
        self._conn.execute("UPDATE sessions SET seen_at = ? WHERE token = ?", (now, token))
        self._conn.commit()

        user = self.get_user(row["user_id"])
        # Hesap kapatilirken oturumlari zaten silinir; bu ikinci kontrol
        # derinlemesine savunmadir: baska bir yoldan olusmus bir oturum
        # kapali hesabi acmasin.
        if user is not None and not user.is_active:
            self.close_session(token)
            return None
        return user

    def close_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._conn.commit()

    def close_all_sessions(self, user_id: int) -> int:
        cursor = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount

    def list_sessions(self, user_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT token, created_at, seen_at, agent FROM sessions "
            "WHERE user_id = ? ORDER BY seen_at DESC",
            (user_id,),
        ).fetchall()
        return [
            {
                # Jetonun kendisi asla donmez; ayirt etmek icin kisa bir on ek.
                "id": r["token"][:8],
                "created_at": r["created_at"],
                "seen_at": r["seen_at"],
                "agent": r["agent"],
            }
            for r in rows
        ]

    def purge_expired(self) -> int:
        now = time.time()
        cursor = self._conn.execute(
            "DELETE FROM sessions WHERE ? - created_at > ? OR ? - seen_at > ?",
            (now, SESSION_MAX_AGE, now, SESSION_IDLE),
        )
        self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------ #
    # Denetim gunlugu
    # ------------------------------------------------------------------ #
    def record(
        self,
        action: str,
        *,
        user: User | None = None,
        username: str | None = None,
        detail: str = "",
        detail_key: str = "",
        detail_args: dict[str, Any] | None = None,
        ip: str = "",
        agent: str = "",
        ok: bool = True,
    ) -> None:
        """Bir islemi gunluge yazar.

        `username` ayrica alinabilir cunku basarisiz girislerde hicbir
        `User` yoktur: DENENEN ad yazilir. Bir gunlukte "bilinmeyen bir
        hesaba on kez girilmeye calisildi" satiri, basarili girisler kadar
        onemlidir.

        Cagiran taraf ne yazdigina dikkat etmeli: `detail` oldugu gibi
        saklanir ve yoneticiye gosterilir. Parola, jeton ve API anahtari
        buraya GECMEZ; uclarin hicbiri body'yi olduğu gibi vermez.

        Yazma hatasi cagiranı dusurmez: gunluge yazamamak, girisin
        basarisiz sayilmasi icin bir sebep degil. Ama sessiz de kalmaz --
        eksik bir denetim gunlugu, dolu sanildigi surece zararlidir.
        """
        try:
            cursor = self._conn.execute(
                "INSERT INTO audit "
                "(at, user_id, username, action, detail, detail_key, detail_args, "
                " ip, agent, ok) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    user.id if user else None,
                    (username if username is not None
                     else (user.username if user else "")),
                    action,
                    detail[:500],
                    detail_key,
                    json.dumps(detail_args or {}, ensure_ascii=False),
                    ip[:64],
                    agent[:200],
                    int(ok),
                ),
            )
            if cursor.lastrowid and int(cursor.lastrowid) % AUDIT_TRIM_EVERY == 0:
                # En yeni AUDIT_KEEP satirin en eskisinden once ne varsa
                # gider. Satir sayisi sinirin altindaysa alt sorgu en kucuk
                # kimligi verir ve hicbir sey silinmez.
                self._conn.execute(
                    "DELETE FROM audit WHERE id < (SELECT MIN(id) FROM "
                    "(SELECT id FROM audit ORDER BY id DESC LIMIT ?))",
                    (AUDIT_KEEP,),
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning(t("auth.audit_failed", action=action, error=str(exc)))

    def list_audit(
        self,
        *,
        limit: int = 200,
        username: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """En yeniden eskiye dogru gunluk satirlari.

        Suzgecler sunucuda uygulanir: 5000 satiri tarayiciya gonderip orada
        elemek, gunlugun buyudugu olcude yavaslardi.
        """
        where: list[str] = []
        params: list[Any] = []
        if username:
            where.append("username = ?")
            params.append(username.strip().lower())
        if action:
            # `run` yazan bir suzgec `run.start` ve `run.stop`u birlikte
            # getirsin: yonetici tur arar, tam ad degil.
            #
            # Iki ayrinti OLCULDU. `%` ve `_` LIKE'in joker karakterleri:
            # kacislanmazsa `action=%` suzgeci "icinde nokta gecen her sey"
            # demeye donusuyor ve bes ilgisiz satir donuyordu. Ve LIKE
            # ASCII'de buyuk/kucuk harf ayirmiyorken `=` ayiriyor, yani
            # `RUN` turu getirip tam adi getirmiyordu. Eylem adlari her
            # zaman kucuk harf; girdiyi de oyle normallestiriyoruz.
            aranan = action.strip().lower()
            kacisli = (
                aranan.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            where.append("(action = ? OR action LIKE ? ESCAPE '\\')")
            params.extend([aranan, f"{kacisli}.%"])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(1, min(int(limit), 1000)))

        rows = self._conn.execute(
            f"SELECT * FROM audit {clause} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        return [
            {
                "id": r["id"],
                "at": r["at"],
                "user_id": r["user_id"],
                "username": r["username"],
                "action": r["action"],
                "detail": r["detail"],
                "detail_key": r["detail_key"],
                "detail_args": _loads(r["detail_args"]),
                "ip": r["ip"],
                "agent": r["agent"],
                "ok": bool(r["ok"]),
            }
            for r in rows
        ]

    def audit_actions(self) -> list[str]:
        """Gunlukte GERCEKTEN gecen islem turleri.

        Suzgec listesi buradan doldurulur: hicbir zaman olmamis bir turu
        secenek olarak sunmak, kullaniciyi bos bir sonuca goturur.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT action FROM audit ORDER BY action"
        ).fetchall()
        return [r["action"] for r in rows]

    def audit_users(self) -> list[str]:
        """Gunlukte adi gecen herkes.

        Kullanici LISTESINDEN degil gunlukten okunur: silinmis bir hesabin
        satirlari da aranabilmeli, ve basarisiz girislerde denenen ad hicbir
        hesaba ait degildir.

        Suzgeclerden BAGIMSIZ: liste o an gorunen satirlardan uretilseydi,
        "kosu" turunu secmek kullanici listesini de kosu baslatmis olanlara
        indirirdi ve ikinci bir suzgec secmek imkansiz olurdu.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT username FROM audit WHERE username <> '' "
            "ORDER BY username"
        ).fetchall()
        return [r["username"] for r in rows]

    def audit_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM audit").fetchone()
        return int(row["n"])


def _loads(raw: str) -> dict[str, Any]:
    """Bozuk parametre metni satiri dusurmez; baslik metnine duseriz."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
