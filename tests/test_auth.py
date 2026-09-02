"""Kimlik dogrulama: parolalar, oturumlar, roller ve korunan uclar.

Bu sunucu dosya yazip kabuk komutu calistirabiliyor; kimlik dogrulama onu
ag uzerinde acilabilir kilan sey. Buradaki her test, o kararlardan birinin
sessizce gevsemesini engeller.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from starlette.testclient import TestClient

from deerx.web.app import build_app
from deerx.web.auth import (
    LOCKOUT_SECONDS,
    MAX_ATTEMPTS,
    MIN_PASSWORD,
    SESSION_COOKIE,
    AuthError,
    AuthStore,
    hash_password,
)


@pytest.fixture
def store(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    yield auth
    auth.close()


@pytest.fixture
def admin(store):
    return store.create_first_admin(
        store.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
    )


class TestPasswordStorage:
    def test_the_password_is_never_stored_in_plain_text(self, store, admin):
        rows = store._conn.execute("SELECT * FROM users").fetchall()
        blob = json.dumps([dict(r) for r in rows], default=str)
        assert "cok-uzun-parola-1" not in blob

    def test_each_user_gets_its_own_salt(self, store, admin):
        store.create_user("ikinci", "cok-uzun-parola-1")
        salts = [r["salt"] for r in store._conn.execute("SELECT salt FROM users")]
        # Ayni parola, farkli tuz -> farkli ozet. Ortak tuz, bir gokkusagi
        # tablosunun tum hesaplari birden acmasi demekti.
        assert len(set(salts)) == 2
        digests = [r["password"] for r in store._conn.execute("SELECT password FROM users")]
        assert len(set(digests)) == 2

    def test_the_same_password_and_salt_reproduce_the_digest(self):
        salt, first = hash_password("parola-parola")
        _, second = hash_password("parola-parola", salt)
        assert first == second

    def test_short_passwords_are_rejected(self, store, admin):
        with pytest.raises(AuthError, match=str(MIN_PASSWORD)):
            store.create_user("kisa", "a" * (MIN_PASSWORD - 1))

    def test_padded_passwords_are_rejected(self, store, admin):
        """Bastaki/sondaki bosluk kopyala-yapistir kazasidir; sessizce kabul
        edilirse kullanici bir daha giremez."""
        with pytest.raises(AuthError):
            store.create_user("bosluklu", "  uzun-parola-var  ")


class TestSetup:
    def test_a_fresh_store_has_no_users(self, store):
        assert store.is_configured is False

    def test_the_setup_token_is_required(self, store):
        store.issue_setup_token()
        with pytest.raises(AuthError, match="jeton"):
            store.create_first_admin("yanlis", "admin", "cok-uzun-parola-1")

    def test_the_token_works_only_once(self, store):
        token = store.issue_setup_token()
        store.create_first_admin(token, "ilk", "cok-uzun-parola-1")
        with pytest.raises(AuthError):
            store.create_first_admin(token, "ikinci", "cok-uzun-parola-1")

    def test_the_first_account_is_a_master_admin(self, store, admin):
        assert admin.role == "admin" and admin.is_master


class TestMasterProtection:
    """Ana yonetici silinemez ve rolu dusurulemez.

    Aksi halde son yonetici kendini kullaniciya cevirip sisteme girilemez
    hale getirebilirdi.
    """

    def test_the_master_cannot_be_demoted(self, store, admin):
        with pytest.raises(AuthError, match="Ana yonetici"):
            store.set_role(admin.id, "user")

    def test_the_master_cannot_be_deleted(self, store, admin):
        with pytest.raises(AuthError, match="Ana yonetici"):
            store.delete_user(admin.id)

    def test_other_admins_can_be_demoted(self, store, admin):
        other = store.create_user("ikinci", "cok-uzun-parola-1", role="admin")
        assert store.set_role(other.id, "user").role == "user"


class TestAuthentication:
    def test_the_right_password_is_accepted(self, store, admin):
        assert store.authenticate("yonetici", "cok-uzun-parola-1").id == admin.id

    def test_the_username_is_case_insensitive(self, store, admin):
        assert store.authenticate("YONETICI", "cok-uzun-parola-1").id == admin.id

    def test_a_wrong_password_is_rejected(self, store, admin):
        with pytest.raises(AuthError):
            store.authenticate("yonetici", "baska-uzun-parola")

    def test_the_error_does_not_reveal_which_half_was_wrong(self, store, admin):
        """Hangi alanin yanlis oldugunu soylemek kullanici sayimina yarar."""
        try:
            store.authenticate("yonetici", "yanlis-uzun-parola")
        except AuthError as exc:
            wrong_password = str(exc)
        try:
            store.authenticate("hicboyleyok", "yanlis-uzun-parola")
        except AuthError as exc:
            unknown_user = str(exc)
        assert wrong_password == unknown_user

    def test_an_unknown_user_still_costs_a_key_derivation(self, store, admin):
        """Yanit suresi "bu kullanici var mi" sorusunu cevaplamamali."""
        start = time.perf_counter()
        with pytest.raises(AuthError):
            store.authenticate("yonetici", "yanlis-uzun-parola")
        known = time.perf_counter() - start

        start = time.perf_counter()
        with pytest.raises(AuthError):
            store.authenticate("hicboyleyok", "yanlis-uzun-parola")
        unknown = time.perf_counter() - start

        # KDF ~100 ms; olmayan kullanici bunun en az yarisi kadar surmeli.
        assert unknown > known * 0.5, f"bilinen {known:.3f}s, bilinmeyen {unknown:.3f}s"


class TestLockout:
    def test_repeated_failures_lock_the_account(self, store, admin):
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(AuthError):
                store.authenticate("yonetici", "yanlis-uzun-parola")
        with pytest.raises(AuthError, match="Cok fazla"):
            store.authenticate("yonetici", "cok-uzun-parola-1")

    def test_a_lockout_does_not_spill_onto_other_accounts(self, store, admin):
        """Aksi halde biri baskasinin hesabini kilitleyebilirdi."""
        store.create_user("ekip", "ikinci-uzun-parola")
        for _ in range(MAX_ATTEMPTS + 1):
            with pytest.raises(AuthError):
                store.authenticate("yonetici", "yanlis-uzun-parola")
        assert store.authenticate("ekip", "ikinci-uzun-parola").username == "ekip"

    def test_a_success_clears_the_counter(self, store, admin):
        for _ in range(MAX_ATTEMPTS - 1):
            with pytest.raises(AuthError):
                store.authenticate("yonetici", "yanlis-uzun-parola")
        store.authenticate("yonetici", "cok-uzun-parola-1")
        with pytest.raises(AuthError, match="hatali"):
            store.authenticate("yonetici", "yanlis-uzun-parola")

    def test_old_failures_age_out(self, store, admin):
        past = time.time() - LOCKOUT_SECONDS - 1
        store._attempts["yonetici"] = [past] * (MAX_ATTEMPTS + 2)
        assert store.authenticate("yonetici", "cok-uzun-parola-1").username == "yonetici"


class TestSessions:
    def test_a_session_resolves_to_its_user(self, store, admin):
        token = store.open_session(admin)
        assert store.resolve_session(token).id == admin.id

    def test_an_unknown_token_resolves_to_nobody(self, store, admin):
        assert store.resolve_session("uydurma") is None
        assert store.resolve_session(None) is None

    def test_logging_out_kills_the_session(self, store, admin):
        token = store.open_session(admin)
        store.close_session(token)
        assert store.resolve_session(token) is None

    def test_changing_the_password_kills_every_session(self, store, admin):
        """Parolayi degistirmenin amaci zaten acik oturumlari kesmektir."""
        first = store.open_session(admin)
        second = store.open_session(admin)
        store.set_password(admin.id, "yepyeni-uzun-parola")
        assert store.resolve_session(first) is None
        assert store.resolve_session(second) is None

    def test_deleting_a_user_kills_their_sessions(self, store, admin):
        other = store.create_user("ekip", "ikinci-uzun-parola")
        token = store.open_session(other)
        store.delete_user(other.id)
        assert store.resolve_session(token) is None

    def test_an_idle_session_expires(self, store, admin):
        token = store.open_session(admin)
        store._conn.execute(
            "UPDATE sessions SET seen_at = ? WHERE token = ?",
            (time.time() - 10 * 24 * 3600, token),
        )
        store._conn.commit()
        assert store.resolve_session(token) is None

    def test_the_absolute_lifetime_is_not_refreshed_by_use(self, store, admin):
        """Bosta kalma suresi tazelenir, mutlak omur tazelenmez."""
        token = store.open_session(admin)
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE token = ?",
            (time.time() - 30 * 24 * 3600, token),
        )
        store._conn.commit()
        assert store.resolve_session(token) is None

    def test_the_token_is_never_listed_back(self, store, admin):
        token = store.open_session(admin, "tarayici")
        listed = store.list_sessions(admin.id)
        assert len(listed) == 1
        assert token not in json.dumps(listed)


# ---------------------------------------------------------------------- #
# HTTP katmani
# ---------------------------------------------------------------------- #
@pytest.fixture
def client(settings):
    """Kullanicisi olmayan sunucu — kimlik dogrulama kapali."""
    with TestClient(build_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def guarded(settings):
    """Bir yoneticisi olan sunucu — kimlik dogrulama devrede."""
    app = build_app(settings)
    with TestClient(app) as client:
        auth = client.app.state.deerx.auth
        auth.create_first_admin(
            auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
        )
        yield client


def _login(client, username="yonetici", password="cok-uzun-parola-1"):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response


class TestGuardedEndpoints:
    PROTECTED = [
        ("get", "/api/overview"),
        ("get", "/api/state/tasks"),
        ("get", "/api/artifacts"),
        ("get", "/api/runs"),
        ("get", "/api/plans"),
        ("get", "/api/users"),
        ("get", "/api/audit"),
        ("post", "/api/run"),
        ("post", "/api/settings"),
        ("post", "/api/package"),
        ("post", "/api/search"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED)
    def test_no_cookie_no_access(self, guarded, method, path):
        response = getattr(guarded, method)(path, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401, f"{method} {path} korumasiz"

    def test_the_event_stream_is_guarded_too(self, guarded):
        """SSE ucu unutulursa kosunun tamami disari akar."""
        assert guarded.get("/api/events?since=-1").status_code == 401

    def test_the_shell_and_static_files_stay_open(self, guarded):
        """Giris ekraninin cizilebilmesi icin kabuk acik kalmali."""
        assert guarded.get("/").status_code == 200
        assert guarded.get("/static/app.js").status_code == 200

    def test_logging_in_opens_the_door(self, guarded):
        assert guarded.get("/api/overview").status_code == 401
        _login(guarded)
        assert guarded.get("/api/overview").status_code == 200

    def test_logging_out_closes_it_again(self, guarded):
        _login(guarded)
        guarded.post("/api/auth/logout")
        assert guarded.get("/api/overview").status_code == 401

    def test_the_session_cookie_is_http_only(self, guarded):
        """JavaScript'ten okunabilen bir oturum cerezi XSS ile calinabilir."""
        response = _login(guarded)
        header = response.headers["set-cookie"]
        assert "httponly" in header.lower()
        assert "samesite=lax" in header.lower()

    def test_a_wrong_password_is_401_not_403(self, guarded):
        response = guarded.post(
            "/api/auth/login", json={"username": "yonetici", "password": "yanlis-uzun"}
        )
        assert response.status_code == 401


class TestUnconfiguredServer:
    """Kullanici yoksa kimlik dogrulama kapali: yerel kurulum bugunku gibi."""

    def test_everything_is_open_without_users(self, client):
        assert client.get("/api/overview").status_code == 200

    def test_status_says_setup_is_needed(self, client):
        status = client.get("/api/auth/status").json()
        assert status["configured"] is False
        assert status["user"] is None

    def test_setup_needs_the_console_token(self, client):
        response = client.post(
            "/api/auth/setup",
            json={"token": "uydurma", "username": "admin", "password": "cok-uzun-parola"},
        )
        assert response.status_code == 403

    def test_setup_creates_the_master_and_logs_in(self, client):
        auth = client.app.state.deerx.auth
        token = auth.issue_setup_token()
        response = client.post(
            "/api/auth/setup",
            json={"token": token, "username": "sahip", "password": "cok-uzun-parola"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["is_master"] is True
        assert client.get("/api/overview").status_code == 200


class TestRoles:
    def test_a_plain_user_cannot_list_accounts(self, guarded):
        _login(guarded)
        guarded.post(
            "/api/users", json={"username": "ekip", "password": "ikinci-uzun-parola"}
        )
        guarded.post("/api/auth/logout")
        _login(guarded, "ekip", "ikinci-uzun-parola")

        assert guarded.get("/api/users").status_code == 403
        assert guarded.post("/api/users", json={}).status_code == 403
        # Ama uygulamanin kendisini kullanabilir.
        assert guarded.get("/api/overview").status_code == 200

    def test_an_admin_cannot_delete_itself(self, guarded):
        me = _login(guarded).json()["user"]
        response = guarded.delete(f"/api/users/{me['id']}")
        assert response.status_code == 400

    def test_the_master_is_protected_over_http(self, guarded):
        me = _login(guarded).json()["user"]
        assert guarded.post(f"/api/users/{me['id']}", json={"role": "user"}).status_code == 400

    def test_changing_your_own_password_needs_the_current_one(self, guarded):
        _login(guarded)
        bad = guarded.post(
            "/api/auth/password",
            json={"current": "yanlis-uzun-parola", "password": "yepyeni-uzun-parola"},
        )
        assert bad.status_code == 403

        good = guarded.post(
            "/api/auth/password",
            json={"current": "cok-uzun-parola-1", "password": "yepyeni-uzun-parola"},
        )
        assert good.status_code == 200
        # Yeni bir cerez verilmis olmali; oturum kopmamali.
        assert guarded.get("/api/overview").status_code == 200

    def test_no_password_or_hash_is_ever_returned(self, guarded):
        _login(guarded)
        payload = json.dumps(guarded.get("/api/users").json())
        assert "cok-uzun-parola-1" not in payload
        assert "password" not in payload
        assert "salt" not in payload


class TestNetworkExposure:
    def test_serving_publicly_without_users_is_refused(self, settings):
        """Kimliksiz bir sunucuyu aga acmak, dosya yazip komut calistirabilen
        bir ucu herkese acmaktir."""
        from deerx.errors import ConfigError
        from deerx.web.app import serve

        with pytest.raises(ConfigError, match="kullanicisiz"):
            serve(settings, host="0.0.0.0", port=0)

    def test_the_cookie_is_marked_secure_over_https(self, settings):
        """HTTPS uzerinden oturum cerezi acik metne dusmemeli."""
        app = build_app(settings)
        # base_url semasi request.url.scheme'i belirler.
        with TestClient(app, base_url="https://deerx.test") as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            response = _login(client)
            assert "secure" in response.headers["set-cookie"].lower()

    def test_the_cookie_is_not_secure_over_plain_http(self, guarded):
        """Duz HTTP'de `Secure` isaretlemek girisi imkansiz kilar.

        Karar eskiden `--host` degerine bakiyordu: loopback disinda her
        zaman `Secure`. Tarayicilar `Secure` cerezi `http://` uzerinden ne
        kaydeder ne gonderir, yani `--host 0.0.0.0` ile acilan bir sunucuya
        dogru parolayla bile girilemiyordu -- belgelenmis ama kullanilamaz
        bir secenek. Artik karar istegin semasindan cikiyor.
        """
        response = _login(guarded)
        assert "secure" not in response.headers["set-cookie"].lower()

    def test_the_cookie_follows_the_request_not_the_bind_address(self, settings):
        """Ayni sunucu, iki istek, iki farkli sonuc.

        Onemli olan nereye baglanildigi degil, istegin nasil geldigi. Bu
        ayni zamanda bugunku bir hatayi kapatiyor: TLS sonlandiran bir
        vekilin arkasinda DeerX 127.0.0.1'e baglanir, eski kural "loopback"
        deyip cerezi `Secure` ISARETLEMEZDI -- baglanti gercekte HTTPS iken.
        """
        kurucu = build_app(settings)
        auth = kurucu.state.deerx.auth
        auth.create_first_admin(auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1")
        kurucu.state.deerx.close()

        # Her sema icin ayri bir uygulama: TestClient baglami kapaninca
        # AppState kendini kapatiyor, ayni uygulamayi iki kez acamayiz.
        for sema, secure_bekleniyor in (("http", False), ("https", True)):
            app = build_app(settings)
            with TestClient(app, base_url=f"{sema}://deerx.test") as client:
                cerez = _login(client).headers["set-cookie"].lower()
            assert ("secure" in cerez) is secure_bekleniyor, f"{sema}: {cerez}"


class TestSessionCookieName:
    def test_the_cookie_name_is_stable(self, guarded):
        _login(guarded)
        assert SESSION_COOKIE in guarded.cookies


class TestPasswordWarnings:
    """Zayif parola reddedilmez, bildirilir.

    Asgari uzunluk NIST SP 800-63B'ye cekildi (8). Ayni rehber karmasiklik
    kurallarina ve keyfi uzunluk sismesine karsi uyarir; bunun yerine bilinen
    parola listeleriyle karsilastirmayi onerir. Kendi makinesinde kendi
    hesabini kuran bir yoneticiye "hayir" demek paternalist olur -- ama riski
    gizlemek de dogru degil.
    """

    def test_a_common_password_is_accepted_with_a_warning(self, store):
        from deerx.web.auth import check_password_policy

        warning = check_password_policy("admin123")
        assert warning is not None
        assert "bilinen listelerde" in warning

    def test_the_warning_reaches_the_caller(self, store):
        store.create_first_admin(store.issue_setup_token(), "admin", "admin123")
        assert store.last_warning is not None

    def test_a_short_but_legal_password_is_flagged(self):
        from deerx.web.auth import check_password_policy

        warning = check_password_policy("g7x!qLm2")
        assert warning is not None and "12 karakter" in warning

    def test_a_strong_password_produces_no_warning(self):
        from deerx.web.auth import check_password_policy

        assert check_password_policy("mor-kunduz-yedi-defter") is None

    def test_below_the_minimum_is_still_refused(self):
        from deerx.web.auth import check_password_policy

        with pytest.raises(AuthError):
            check_password_policy("kisa12")

    def test_the_check_is_case_insensitive(self):
        from deerx.web.auth import check_password_policy

        assert check_password_policy("ADMIN123") is not None
        assert check_password_policy("Password1") is not None

    def test_the_api_returns_the_warning_on_setup(self, client):
        auth = client.app.state.deerx.auth
        response = client.post(
            "/api/auth/setup",
            json={
                "token": auth.issue_setup_token(),
                "username": "admin",
                "password": "admin123",
            },
        )
        assert response.status_code == 200
        assert response.json()["warning"]

    def test_the_api_returns_the_warning_when_creating_a_user(self, guarded):
        _login(guarded)
        response = guarded.post(
            "/api/users", json={"username": "ekip", "password": "password123"}
        )
        assert response.status_code == 200
        assert response.json()["warning"]

    def test_no_warning_for_a_strong_new_user(self, guarded):
        _login(guarded)
        response = guarded.post(
            "/api/users", json={"username": "ekip", "password": "mor-kunduz-yedi-defter"}
        )
        assert response.json()["warning"] is None


class TestAccountSuspension:
    """Hesabi silmeden kapatmak.

    Kapatmak silmek degildir: ayrilan biri geri donebilir ve hesabi silmek
    gecmisteki izlerini de anlamsizlastirirdi.
    """

    def test_a_new_account_starts_open(self, store, admin):
        assert admin.is_active is True

    def test_a_closed_account_cannot_log_in(self, store, admin):
        other = store.create_user("ekip", "ikinci-uzun-parola")
        store.set_active(other.id, False)
        with pytest.raises(AuthError, match="kapatilmis"):
            store.authenticate("ekip", "ikinci-uzun-parola")

    def test_reopening_restores_access(self, store, admin):
        other = store.create_user("ekip", "ikinci-uzun-parola")
        store.set_active(other.id, False)
        store.set_active(other.id, True)
        assert store.authenticate("ekip", "ikinci-uzun-parola").username == "ekip"

    def test_closing_drops_open_sessions_at_once(self, store, admin):
        """Yoksa kapatma islemi, kullanici cikis yapana kadar hicbir sey
        yapmamis olurdu."""
        other = store.create_user("ekip", "ikinci-uzun-parola")
        token = store.open_session(other)
        assert store.resolve_session(token) is not None

        store.set_active(other.id, False)
        assert store.resolve_session(token) is None

    def test_a_session_for_a_closed_account_is_refused(self, store, admin):
        """Derinlemesine savunma: baska bir yoldan olusmus bir oturum bile
        kapali hesabi acmamali."""
        other = store.create_user("ekip", "ikinci-uzun-parola")
        store._conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (other.id,))
        store._conn.commit()
        token = store.open_session(other)
        assert store.resolve_session(token) is None

    def test_the_closed_state_does_not_leak_on_a_wrong_password(self, store, admin):
        """Kapali oldugunu yalnizca dogru parolayi bilen ogrenmeli; aksi
        halde parola bilmeden hesap durumu ogrenilebilirdi."""
        other = store.create_user("ekip", "ikinci-uzun-parola")
        store.set_active(other.id, False)
        with pytest.raises(AuthError, match="hatali"):
            store.authenticate("ekip", "yanlis-uzun-parola")

    def test_the_master_cannot_be_closed(self, store, admin):
        with pytest.raises(AuthError, match="Ana yonetici"):
            store.set_active(admin.id, False)

    def test_reopening_clears_an_old_lockout(self, store, admin):
        """Kapali kalmis bir hesap, eski basarisiz denemeler yuzunden
        acildiginda kilitli olmamali."""
        other = store.create_user("ekip", "ikinci-uzun-parola")
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(AuthError):
                store.authenticate("ekip", "yanlis-uzun-parola")
        store.set_active(other.id, False)
        store.set_active(other.id, True)
        assert store.authenticate("ekip", "ikinci-uzun-parola").username == "ekip"


class TestSuspensionOverHttp:
    def _make_user(self, client, username="ekip", password="ikinci-uzun-parola"):
        response = client.post(
            "/api/users", json={"username": username, "password": password}
        )
        assert response.status_code == 200
        return response.json()["user"]

    def test_an_admin_can_close_and_open_an_account(self, guarded):
        _login(guarded)
        user = self._make_user(guarded)

        closed = guarded.post(f"/api/users/{user['id']}", json={"active": False})
        assert closed.status_code == 200
        assert closed.json()["user"]["is_active"] is False

        opened = guarded.post(f"/api/users/{user['id']}", json={"active": True})
        assert opened.json()["user"]["is_active"] is True

    def test_a_closed_user_is_locked_out_of_the_api(self, guarded):
        _login(guarded)
        user = self._make_user(guarded)
        guarded.post(f"/api/users/{user['id']}", json={"active": False})
        guarded.post("/api/auth/logout")

        response = guarded.post(
            "/api/auth/login",
            json={"username": "ekip", "password": "ikinci-uzun-parola"},
        )
        assert response.status_code == 401
        assert "kapatilmis" in response.json()["error"]

    def test_closing_ends_the_users_live_session(self, guarded):
        """Kapatma aninda calisan bir oturum varsa o da dusmeli."""
        _login(guarded)
        user = self._make_user(guarded)
        auth = guarded.app.state.deerx.auth
        victim = auth.open_session(auth.get_user(user["id"]))
        assert auth.resolve_session(victim) is not None

        guarded.post(f"/api/users/{user['id']}", json={"active": False})
        assert auth.resolve_session(victim) is None

    def test_an_admin_cannot_close_itself(self, guarded):
        me = _login(guarded).json()["user"]
        response = guarded.post(f"/api/users/{me['id']}", json={"active": False})
        assert response.status_code == 400
        assert "Kendi hesabinizi" in response.json()["error"]

    def test_the_master_is_protected_from_other_admins(self, guarded):
        me = _login(guarded).json()["user"]
        guarded.post(
            "/api/users",
            json={"username": "ikinci", "password": "ucuncu-uzun-parola", "role": "admin"},
        )
        guarded.post("/api/auth/logout")
        _login(guarded, "ikinci", "ucuncu-uzun-parola")
        response = guarded.post(f"/api/users/{me['id']}", json={"active": False})
        assert response.status_code == 400

    def test_a_plain_user_cannot_close_anyone(self, guarded):
        _login(guarded)
        victim = self._make_user(guarded, "kurban", "kurban-uzun-parola")
        self._make_user(guarded, "sirada", "sirada-uzun-parola")
        guarded.post("/api/auth/logout")
        _login(guarded, "sirada", "sirada-uzun-parola")

        blocked = guarded.post(f"/api/users/{victim['id']}", json={"active": False})
        assert blocked.status_code == 403

    def test_the_listing_shows_the_state(self, guarded):
        _login(guarded)
        user = self._make_user(guarded)
        guarded.post(f"/api/users/{user['id']}", json={"active": False})
        rows = {u["username"]: u for u in guarded.get("/api/users").json()["users"]}
        assert rows["ekip"]["is_active"] is False
        assert rows["yonetici"]["is_active"] is True


class TestSuspensionMigration:
    def test_a_database_without_the_column_still_opens(self, tmp_path):
        """`is_active` sonradan eklendi; mevcut kurulumlar cokmemeli."""
        import sqlite3

        db = tmp_path / "eski.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                salt BLOB NOT NULL,
                password BLOB NOT NULL,
                created_at REAL NOT NULL,
                last_login REAL,
                is_master INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users (username, role, salt, password, created_at, is_master)
            VALUES ('eski', 'admin', X'00', X'00', 0, 1);
            """
        )
        conn.commit()
        conn.close()

        store = AuthStore(db)
        try:
            user = store.find("eski")
            # Mevcut hesaplar acik sayilir; gecis kimseyi disarida birakmaz.
            assert user is not None and user.is_active is True
        finally:
            store.close()


# ---------------------------------------------------------------------- #
# Denetim gunlugu
# ---------------------------------------------------------------------- #
class TestAuditLog:
    """Kim, ne zaman, ne yapti.

    `users` ve `sessions` "su an kim var" sorusunu cevaplar. Bu tablo
    geriye donuk okunur ve ondan bagimsiz yasar: silinen bir hesabin izi
    kalmali, reddedilen bir giris denemesi yazilmali.
    """

    def test_a_recorded_action_comes_back(self, store, admin):
        store.record("login", user=admin, ip="10.0.0.5", agent="Firefox")
        (satir,) = store.list_audit()
        assert satir["action"] == "login"
        assert satir["username"] == "yonetici"
        assert satir["user_id"] == admin.id
        assert satir["ip"] == "10.0.0.5"
        assert satir["ok"] is True

    def test_the_newest_row_comes_first(self, store, admin):
        for n in range(3):
            store.record("run.start", user=admin, detail=str(n))
        assert [r["detail"] for r in store.list_audit()] == ["2", "1", "0"]

    def test_a_refused_attempt_is_recorded_under_the_name_that_was_tried(self, store):
        """Var olmayan bir hesap icin de satir yazilir.

        "Bilinmeyen bir adla on kez denendi" bir guvenlik gunlugunun en
        cok ise yarayan satiridir; hicbir `User` olmadigi icin en kolay
        atlanacak olan da odur.
        """
        store.record("login.failed", username="kokyonetici", ok=False)
        (satir,) = store.list_audit()
        assert satir["username"] == "kokyonetici"
        assert satir["user_id"] is None
        assert satir["ok"] is False

    def test_deleting_a_user_keeps_their_trail(self, store, admin):
        gecici = store.create_user("gecici", "cok-uzun-parola-1")
        store.record("login", user=gecici)
        store.delete_user(gecici.id)

        (satir,) = store.list_audit(username="gecici")
        # Ad METIN olarak kalir: hesabi silmek, gecmisi temizlemenin yolu
        # olmamali.
        assert satir["username"] == "gecici"
        # Bag kopar: bosalan bir kimlik yanlis bir hesaba isaret etmesin.
        assert satir["user_id"] is None

    def test_the_user_filter_narrows_the_list(self, store, admin):
        ikinci = store.create_user("ikinci", "cok-uzun-parola-1")
        store.record("login", user=admin)
        store.record("login", user=ikinci)
        assert len(store.list_audit()) == 2
        assert [r["username"] for r in store.list_audit(username="ikinci")] == ["ikinci"]

    def test_the_action_filter_takes_a_whole_kind(self, store, admin):
        """`run` yazan bir suzgec `run.start` ve `run.stop`u birlikte
        getirir: yonetici tur arar, tam ad degil."""
        store.record("run.start", user=admin)
        store.record("run.stop", user=admin)
        store.record("login", user=admin)
        assert {r["action"] for r in store.list_audit(action="run")} == {
            "run.start", "run.stop"
        }

    def test_the_action_filter_matches_whole_kinds_not_prefixes(self, store, admin):
        """Yarim bir sozcuk hicbir seyi getirmemeli.

        Suzgec `LIKE 'log%'` olsaydi `login`, `login.failed` ve `logout`
        birden gelirdi -- kullaniciya "log turu" diye bir sey oldugunu
        dusundururdu. Eslesme ya TAM addir ya da `ad.` onekidir.
        """
        store.record("login", user=admin)
        store.record("login.failed", username="deneme", ok=False)
        store.record("logout", user=admin)
        assert store.list_audit(action="log") == []
        assert {r["action"] for r in store.list_audit(action="login")} == {
            "login", "login.failed"
        }

    def test_the_filters_and_the_limit_combine(self, store, admin):
        """Uc suzgec ayni anda calismali.

        `list_audit` sorguyu parca parca kuruyor ve parametreleri sirayla
        ekliyor; `action` suzgeci IKI parametre birden ekledigi icin
        `limit`in sona kalmasi tesadufe birakilamaz. Yanlis sirada bir
        parametre sessizce baska bir satir kumesi dondururdu.
        """
        ikinci = store.create_user("ikinci", "cok-uzun-parola-1")
        for kisi in (admin, ikinci):
            for eylem in ("run.start", "run.stop", "login", "logout"):
                store.record(eylem, user=kisi)

        ikili = store.list_audit(username="ikinci", action="run", limit=100)
        assert len(ikili) == 2
        assert all(r["username"] == "ikinci" for r in ikili)
        assert all(r["action"].startswith("run") for r in ikili)

        # Limit de birlikte uygulanmali, en yeniden baslayarak.
        tek = store.list_audit(username="ikinci", action="run", limit=1)
        assert len(tek) == 1 and tek[0]["action"] == "run.stop"

    def test_like_wildcards_in_the_filter_are_escaped(self, store, admin):
        """`%` ve `_` LIKE'in joker karakterleri.

        OLCULDU: kacislanmadiklarinda `action=%` suzgeci "icinde nokta
        gecen her sey" demeye donusuyordu -- bes ilgisiz satir. Yonetici
        suzgec kutusuna ne yazarsa yazsin, anlamsiz bir alt kume degil
        BOS sonuc gormeli.
        """
        for eylem in ("login", "run.start", "run.stop", "settings.change"):
            store.record(eylem, user=admin)
        for joker in ("%", "_", "%.%", "\\"):
            assert store.list_audit(action=joker) == [], joker

    def test_the_action_filter_ignores_case_consistently(self, store, admin):
        """OLCULDU: `=` buyuk/kucuk harf ayiriyor, LIKE ASCII'de
        ayirmiyor. `RUN` turu getirip tam adi getirmiyordu -- ayni
        suzgecin iki yarisi farkli davraniyordu. Eylem adlari her zaman
        kucuk harf; girdi de oyle normallestiriliyor.
        """
        store.record("run", user=admin)
        store.record("run.start", user=admin)
        for yazim in ("RUN", "  Run  ", "run"):
            assert {r["action"] for r in store.list_audit(action=yazim)} == {
                "run", "run.start"
            }, yazim

    def test_the_kinds_come_from_what_is_actually_in_the_log(self, store, admin):
        store.record("login", user=admin)
        store.record("run.start", user=admin)
        assert store.audit_actions() == ["login", "run.start"]

    def test_a_broken_detail_does_not_drop_the_row(self, store, admin):
        """Bozuk parametre metni satiri yutmamali: baslik metnine duseriz."""
        store.record("run.start", user=admin, detail="Plan: Mobil")
        store._conn.execute("UPDATE audit SET detail_args = 'bu json degil'")
        store._conn.commit()
        (satir,) = store.list_audit()
        assert satir["detail"] == "Plan: Mobil"
        assert satir["detail_args"] == {}

    def test_the_log_stops_growing(self, store, admin):
        """Gunluk proje veritabaniyla ayni dosyayi paylasiyor; sinirsiz
        buyuyemez.

        Budama ARALIKLI: her yazmada 5000 satiri taramak, sinirin her
        zaman tam tutulmasi icin odenecek fazla bir bedel olurdu. Ust
        sinir bu yuzden bir budama araligi kadar yukaridadir.
        """
        from deerx.web.auth import AUDIT_KEEP, AUDIT_TRIM_EVERY

        for _ in range(AUDIT_KEEP + 2 * AUDIT_TRIM_EVERY):
            store.record("login", user=admin)
        assert store.audit_count() <= AUDIT_KEEP + AUDIT_TRIM_EVERY

    def test_trimming_drops_the_oldest_rows_first(self, store, admin):
        from deerx.web.auth import AUDIT_KEEP, AUDIT_TRIM_EVERY

        toplam = AUDIT_KEEP + AUDIT_TRIM_EVERY + 1
        for n in range(toplam):
            store.record("login", user=admin, detail=str(n))
        kalanlar = {int(r["detail"]) for r in store.list_audit(limit=1000)}
        # En yeni satir durmali, en eskisi gitmis olmali.
        assert toplam - 1 in kalanlar
        assert 0 not in kalanlar

    def test_the_limit_is_capped(self, store, admin):
        """Sinirsiz bir `limit`, tek bir istekle butun gunlugu cekmenin
        yolu olurdu. Istenen sayi ne olursa olsun tavan var."""
        for _ in range(1005):
            store.record("login", user=admin)
        assert len(store.list_audit(limit=10**9)) == 1000

    def test_a_write_failure_does_not_take_the_caller_down(self, store, admin):
        """Gunluge yazamamak, girisin basarisiz sayilmasi icin sebep degil."""
        store._conn.execute("DROP TABLE audit")
        store._conn.commit()
        store.record("login", user=admin)  # patlamamali


class TestAuditEndpoint:
    """Gunlugu okuyan uc ve onu dolduran islemler."""

    def test_a_plain_user_cannot_read_it(self, guarded):
        """Gunlukte kimin ne zaman girdigi, hangi adresten geldigi yaziyor.
        Bunu her kullaniciya acmak, gunlugun kendisini bir bilgi sizintisi
        haline getirirdi."""
        auth = guarded.app.state.deerx.auth
        auth.create_user("ekip", "cok-uzun-parola-1")
        _login(guarded, "ekip", "cok-uzun-parola-1")
        assert guarded.get("/api/audit").status_code == 403

    def test_an_admin_can_read_it(self, guarded):
        _login(guarded)
        assert guarded.get("/api/audit").status_code == 200

    def test_it_stays_open_where_there_is_no_login_at_all(self, client):
        """Hic kullanici yokken sunucunun tamami zaten acik. Gunlugu tek
        basina kapatmak hicbir sey korumaz, yalnizca yerel kurulumda
        paneli olu birakirdi."""
        assert client.get("/api/audit").status_code == 200

    def test_signing_in_is_recorded(self, guarded):
        _login(guarded)
        entries = guarded.get("/api/audit").json()["entries"]
        assert entries[0]["action"] == "login"
        assert entries[0]["username"] == "yonetici"
        assert entries[0]["ok"] is True

    def test_a_refused_sign_in_is_recorded(self, guarded):
        guarded.post(
            "/api/auth/login", json={"username": "KokYonetici", "password": "x" * 12}
        )
        _login(guarded)
        red = [e for e in guarded.get("/api/audit").json()["entries"]
               if e["action"] == "login.failed"]
        assert len(red) == 1
        # Ad kucultulur: `kokyonetici` ile `KokYonetici` ayni denemedir ve
        # suzgecte iki ayri kullanici gibi gorunmemeli.
        assert red[0]["username"] == "kokyonetici"
        assert red[0]["ok"] is False

    def test_signing_out_is_recorded(self, guarded):
        _login(guarded)
        guarded.post("/api/auth/logout")
        _login(guarded)
        actions = [e["action"] for e in guarded.get("/api/audit").json()["entries"]]
        assert "logout" in actions

    def test_a_run_is_recorded_with_a_translatable_title(self, guarded, settings):
        """"Ne calistirmis" sorusunun cevabi. Baslik ANAHTAR olarak da
        saklanir: gunluk, satirin yazildigi gunun dilinde donmasin."""
        settings.anthropic_api_key = None
        _login(guarded)
        assert guarded.post(
            "/api/run", json={"phase": "ingest", "force": True}
        ).status_code == 200

        kosu = [e for e in guarded.get("/api/audit").json()["entries"]
                if e["action"] == "run.start"]
        assert len(kosu) == 1
        assert kosu[0]["username"] == "yonetici"
        assert kosu[0]["detail_key"] == "runs.titlePhase"
        assert kosu[0]["detail_args"] == {"phase": "ingest"}

    def test_a_settings_change_records_names_but_never_values(self, guarded):
        """Bir denetim gunlugu, sizdirdigi anda korudugu seyin karsisina
        gecer. Degisen ALAN yazilir, degeri asla."""
        _login(guarded)
        gizli = "sk-cok-gizli-anahtar-123456"
        assert guarded.post(
            "/api/settings", json={"openai_api_key": gizli, "max_tokens": 4096}
        ).status_code == 200

        govde = guarded.get("/api/audit").text
        assert gizli not in govde
        satir = [e for e in guarded.get("/api/audit").json()["entries"]
                 if e["action"] == "settings.change"][0]
        assert "openai_api_key" in satir["detail"]

    def test_the_filters_reach_the_store(self, guarded):
        settings_ok = _login(guarded)
        assert settings_ok.status_code == 200
        guarded.post("/api/auth/logout")
        _login(guarded)

        yalniz = guarded.get("/api/audit?action=logout").json()["entries"]
        assert {e["action"] for e in yalniz} == {"logout"}
        kimse = guarded.get("/api/audit?user=hicboyleyok").json()["entries"]
        assert kimse == []

    def test_the_kinds_are_offered_for_filtering(self, guarded):
        _login(guarded)
        assert "login" in guarded.get("/api/audit").json()["actions"]

    def test_deleting_a_user_does_not_erase_what_they_did(self, guarded):
        """Hesabi silmek, gecmisi temizlemenin yolu olmamali."""
        auth = guarded.app.state.deerx.auth
        ekip = auth.create_user("ekip", "cok-uzun-parola-1")
        _login(guarded, "ekip", "cok-uzun-parola-1")
        guarded.post("/api/auth/logout")

        _login(guarded)
        assert guarded.delete(f"/api/users/{ekip.id}").status_code == 200
        kalan = guarded.get("/api/audit?user=ekip").json()["entries"]
        assert [e["action"] for e in kalan] == ["logout", "login"]

    def test_the_filter_lists_do_not_narrow_themselves(self, guarded):
        """Bir turu secmek kullanici listesini daraltmamali.

        Listeler o an GORUNEN satirlardan uretilseydi, "kosu"yu secmek
        kullanici suzgecini kosu baslatmis olanlara indirir ve ikinci bir
        suzgec secmek imkansiz olurdu -- kullanici da listenin neden
        degistigini anlamazdi.
        """
        auth = guarded.app.state.deerx.auth
        auth.create_user("ekip", "cok-uzun-parola-1")
        _login(guarded, "ekip", "cok-uzun-parola-1")
        guarded.post("/api/auth/logout")
        _login(guarded)

        hepsi = guarded.get("/api/audit").json()
        suzulmus = guarded.get("/api/audit?action=logout").json()
        assert suzulmus["users"] == hepsi["users"]
        assert suzulmus["actions"] == hepsi["actions"]
        # Suzgec yine de SATIRLARI daraltmali; sabit kalan sadece listeler.
        assert len(suzulmus["entries"]) < len(hepsi["entries"])

    def test_the_user_list_remembers_people_who_are_gone(self, guarded):
        """Silinen bir hesabin adi suzgecte kalmali: satirlari duruyor."""
        auth = guarded.app.state.deerx.auth
        ekip = auth.create_user("ekip", "cok-uzun-parola-1")
        _login(guarded, "ekip", "cok-uzun-parola-1")
        _login(guarded)
        guarded.delete(f"/api/users/{ekip.id}")
        assert "ekip" in guarded.get("/api/audit").json()["users"]

    def test_a_refused_name_is_offered_for_filtering(self, guarded):
        """Var olmayan bir hesaba yapilan denemeler de suzulebilmeli:
        yoneticinin bakmak isteyecegi ilk sey odur."""
        guarded.post(
            "/api/auth/login", json={"username": "kokyonetici", "password": "x" * 12}
        )
        _login(guarded)
        assert "kokyonetici" in guarded.get("/api/audit").json()["users"]


# ---------------------------------------------------------------------- #
# Parola ayarlama: CLI
# ---------------------------------------------------------------------- #
class TestPasswordFromStdin:
    """Parolayi standart girdiden alan yol.

    `getpass` Windows'ta konsolu DOGRUDAN okur ve boru hattindaki veriyi
    hic gormez: `printf ... | deerx user passwd admin` ciktisiz kilitlenir.
    Kullanicinin "sifre degistirme calismiyor" dedigi sey buydu -- bir
    betikten beslenemiyordu. `--stdin` o yolu aciyor.
    """

    @staticmethod
    def _kos(calisma, *args, girdi=""):
        import subprocess
        import sys

        # `python -m deerx` yok (`__main__.py` bulunmuyor) ve kurulu
        # konsol betiginin adi platforma gore degisiyor; uygulamayi
        # dogrudan cagirmak ikisine de bagli degil.
        return subprocess.run(
            [sys.executable, "-c", "from deerx.cli import app; app()", *args],
            capture_output=True, text=True, timeout=120,
            cwd=str(calisma), input=girdi,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    @pytest.fixture()
    def calisma(self, tmp_path):
        (tmp_path / "deerx.toml").write_text(
            '[deerx]\nlanguage = "tr"\n', encoding="utf-8"
        )
        return tmp_path

    def test_it_creates_the_first_admin_when_there_is_no_database(self, calisma):
        """"DB hic yoksa kursun" -- ana yonetici, silinemez."""
        sonuc = self._kos(
            calisma, "user", "ensure", "admin", "--stdin", girdi="cok-uzun-parola-1\n"
        )
        assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr

        store = AuthStore(calisma / ".deerx" / "deerx.db")
        try:
            user = store.find("admin")
            assert user is not None and user.is_master and user.role == "admin"
            assert store.authenticate("admin", "cok-uzun-parola-1").id == user.id
        finally:
            store.close()

    def test_it_resets_an_existing_password(self, calisma):
        self._kos(calisma, "user", "ensure", "admin", "--stdin", girdi="ilk-uzun-parola\n")
        sonuc = self._kos(
            calisma, "user", "ensure", "admin", "--stdin", girdi="ikinci-uzun-parola\n"
        )
        assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr

        store = AuthStore(calisma / ".deerx" / "deerx.db")
        try:
            store.authenticate("admin", "ikinci-uzun-parola")
            with pytest.raises(AuthError):
                store.authenticate("admin", "ilk-uzun-parola")
        finally:
            store.close()

    def test_it_adds_a_second_admin(self, calisma):
        self._kos(calisma, "user", "ensure", "admin", "--stdin", girdi="cok-uzun-parola-1\n")
        self._kos(calisma, "user", "ensure", "sarpel", "--stdin", girdi="baska-uzun-parola\n")

        store = AuthStore(calisma / ".deerx" / "deerx.db")
        try:
            ikinci = store.find("sarpel")
            assert ikinci is not None and ikinci.role == "admin"
            # Ana yonetici ILK hesaptir; ikincisi devralmaz.
            assert not ikinci.is_master
        finally:
            store.close()

    def test_passwd_also_takes_stdin(self, calisma):
        self._kos(calisma, "user", "ensure", "admin", "--stdin", girdi="ilk-uzun-parola\n")
        sonuc = self._kos(
            calisma, "user", "passwd", "admin", "--stdin", girdi="yeni-uzun-parola\n"
        )
        assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr

        store = AuthStore(calisma / ".deerx" / "deerx.db")
        try:
            store.authenticate("admin", "yeni-uzun-parola")
        finally:
            store.close()

    def test_an_empty_stdin_says_so_instead_of_blaming_the_password(self, calisma):
        """Bos bir boru hatti "parola gelmedi" demeli.

        Politika onu zaten reddederdi -- ama "en az 8 karakter olmali"
        diyerek. Bu, borusu yanlis baglanmis bir betigi kovalayan kisiyi
        parolanin uzunluguna baktirir; sorun orada degildir.
        """
        sonuc = self._kos(calisma, "user", "ensure", "admin", "--stdin", girdi="")
        assert sonuc.returncode != 0
        cikti = sonuc.stdout + sonuc.stderr
        assert "girdiden parola gelmedi" in cikti, cikti
        assert "8 karakter" not in cikti, cikti

    def test_the_password_policy_still_applies(self, calisma):
        sonuc = self._kos(calisma, "user", "ensure", "admin", "--stdin", girdi="kisa\n")
        assert sonuc.returncode != 0

    def test_only_the_line_ending_is_stripped(self, calisma):
        """`strip()` bastaki/sondaki bosluklu bir parolayi sessizce
        baskasina cevirirdi. Politika onu zaten reddediyor; reddi gormek
        sessiz degisiklikten iyidir."""
        sonuc = self._kos(
            calisma, "user", "ensure", "admin", "--stdin", girdi="  bosluklu-parola  \n"
        )
        assert sonuc.returncode != 0, sonuc.stdout

    def test_the_password_never_reaches_the_command_line(self):
        """Arguman `ps` ciktisinda ve Gorev Yoneticisi'nde gorunur.

        Sozlesme: parola YALNIZCA standart girdiden alinir. Bir gun
        `--password` eklenirse bu test dusertir.
        """
        import inspect

        from deerx import cli

        for komut in (cli.user_add, cli.user_passwd, cli.user_ensure):
            parametreler = inspect.signature(komut).parameters
            assert "password" not in parametreler, komut.__name__
