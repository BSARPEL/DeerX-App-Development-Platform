"""Proje kaydi ve iki katmanli yetki.

Bir proje kayitli bir DIZINDIR: kimligi platform veritabaninda bir
satir, verisi kendi dosyasinda. Buradaki testler o kararin dayandigi
kurallari kilitliyor -- ozellikle "iki kayit ayni dizini gosteremez"
ve "sahipsiz proje birakilmaz".
"""

from __future__ import annotations

import pytest

from deerx.web.projects import (
    PROJECT_ROLES,
    ProjectError,
    ProjectStore,
    role_at_least,
    slugify,
)


@pytest.fixture
def store(tmp_path):
    depo = ProjectStore(tmp_path / "platform.db")
    yield depo
    depo.close()


@pytest.fixture
def alan(tmp_path):
    def kur(ad: str):
        yol = tmp_path / ad
        yol.mkdir()
        return yol
    return kur


class TestKayit:
    def test_a_directory_becomes_a_project(self, store, alan):
        proje = store.create(alan("mobil"), owner_id=1)
        assert proje.name == "mobil"
        assert proje.slug == "mobil"
        assert proje.path == (alan("mobil2").parent / "mobil").resolve()
        assert not proje.archived

    def test_the_directory_is_not_created_or_touched(self, store, tmp_path):
        """Kayit, var olan bir seyin uzerine konan bir etikettir.

        Dizini olusturmak ya da icine yazmak, kaydin sessizce bir kurulum
        adimina donusmesi olurdu.
        """
        yok = tmp_path / "henuz-yok"
        store.create(yok, owner_id=1)
        assert not yok.exists()

    def test_the_same_directory_cannot_be_registered_twice(self, store, alan):
        """Iki kayit ayni dizini gosterirse iki proje ayni veritabanini
        paylasir ve biri otekinin gorevlerini gorur."""
        yol = alan("ortak")
        store.create(yol, owner_id=1)
        with pytest.raises(ProjectError):
            store.create(yol, owner_id=2)

    def test_slugs_do_not_collide(self, store, tmp_path):
        for i in range(3):
            yol = tmp_path / f"k{i}" / "Mobil Uygulama"
            yol.mkdir(parents=True)
            store.create(yol, owner_id=1)
        sluglar = [p.slug for p in store.all_projects()]
        assert sluglar == sorted(sluglar)
        assert len(set(sluglar)) == 3
        assert "mobil-uygulama" in sluglar

    def test_turkish_letters_survive_slugging(self):
        assert slugify("Çağrı Merkezi") == "cagri-merkezi"
        assert slugify("İş Akışı") == "is-akisi"
        assert slugify("!!!") == "proje"

    def test_the_creator_becomes_the_owner(self, store, alan):
        proje = store.create(alan("mobil"), owner_id=7)
        assert store.role_of(proje.id, 7) == "owner"


class TestUyelik:
    def test_a_member_only_sees_their_own_projects(self, store, alan):
        benim = store.create(alan("benim"), owner_id=1)
        store.create(alan("baskasinin"), owner_id=2)

        gorunen = store.for_user(1)
        assert [p.id for p in gorunen] == [benim.id]
        assert gorunen[0].role == "owner"

    def test_a_platform_admin_sees_everything(self, store, alan):
        """Yoksa sahibi ayrilmis bir proje kimsenin ulasamadigi bir
        dizine donusurdu."""
        store.create(alan("benim"), owner_id=1)
        store.create(alan("baskasinin"), owner_id=2)

        gorunen = store.for_user(9, is_admin=True)
        assert len(gorunen) == 2
        # Uyeligi olmayan yonetici gordugu her seye mudahale edebilmeli.
        assert {p.role for p in gorunen} == {"owner"}

    def test_an_admin_keeps_their_real_role_where_they_have_one(self, store, alan):
        proje = store.create(alan("proje"), owner_id=1)
        store.set_member(proje.id, 9, "viewer")
        gorunen = store.for_user(9, is_admin=True)
        assert gorunen[0].role == "viewer"

    def test_the_last_owner_cannot_be_removed(self, store, alan):
        """Sahipsiz bir proje yalnizca platform yoneticisinin
        ulasabildigi bir dizine donusur; bunu kaza eseri yapmak kolay
        olmamali."""
        proje = store.create(alan("proje"), owner_id=1)
        store.set_member(proje.id, 2, "developer")
        with pytest.raises(ProjectError):
            store.remove_member(proje.id, 1)

        store.set_member(proje.id, 2, "owner")
        assert store.remove_member(proje.id, 1) is True

    def test_an_unknown_role_is_refused(self, store, alan):
        proje = store.create(alan("proje"), owner_id=1)
        with pytest.raises(ProjectError):
            store.set_member(proje.id, 2, "patron")

    def test_changing_a_role_keeps_the_join_date(self, store, alan):
        proje = store.create(alan("proje"), owner_id=1)
        store.set_member(proje.id, 2, "viewer")
        once = store.members(proje.id)[-1]["added_at"]
        store.set_member(proje.id, 2, "developer")
        sonra = store.members(proje.id)[-1]
        assert sonra["role"] == "developer"
        assert sonra["added_at"] == once

    def test_deleting_an_account_clears_its_memberships(self, store, alan):
        birinci = store.create(alan("bir"), owner_id=1)
        ikinci = store.create(alan("iki"), owner_id=1)
        store.set_member(birinci.id, 5, "developer")
        store.set_member(ikinci.id, 5, "viewer")

        assert store.forget_user(5) == 2
        assert store.for_user(5) == []


class TestArsiv:
    def test_archiving_hides_without_deleting(self, store, alan):
        """Bir projeyi silmek, orada yapilmis her seyin gecmisini de
        silmek olurdu."""
        proje = store.create(alan("proje"), owner_id=1)
        store.set_archived(proje.id, True)

        assert store.for_user(1) == []
        arsivli = store.for_user(1, include_archived=True)
        assert [p.id for p in arsivli] == [proje.id]
        assert arsivli[0].archived

        store.set_archived(proje.id, False)
        assert len(store.for_user(1)) == 1

    def test_the_directory_survives_archiving(self, store, alan):
        yol = alan("proje")
        (yol / "dosya.txt").write_text("veri", encoding="utf-8")
        proje = store.create(yol, owner_id=1)
        store.set_archived(proje.id, True)
        assert (yol / "dosya.txt").read_text(encoding="utf-8") == "veri"


class TestRolSirasi:
    def test_roles_are_ordered_from_weak_to_strong(self):
        assert PROJECT_ROLES == ("viewer", "developer", "owner")

    @pytest.mark.parametrize("role,needed,beklenen", [
        ("owner", "viewer", True),
        ("owner", "owner", True),
        ("developer", "viewer", True),
        ("developer", "owner", False),
        ("viewer", "developer", False),
        ("", "viewer", False),
        ("owner", "patron", False),
    ])
    def test_at_least(self, role, needed, beklenen):
        assert role_at_least(role, needed) is beklenen


class TestRolHttpUzerinde:
    """Proje rolu gercekten kapiyi tutuyor mu.

    Iki katmanli yetkinin ANLAMI burada olculur: bir izleyici projeyi
    GORUR ama degistiremez; bir gelistirici calisir ama kimin girecegine
    karar vermez.
    """

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            yield client

    @staticmethod
    def _giris(client, ad="yonetici", parola="cok-uzun-parola-1"):
        cevap = client.post(
            "/api/auth/login", json={"username": ad, "password": parola}
        )
        assert cevap.status_code == 200, cevap.text
        return cevap

    def _izleyici_ac(self, client):
        self._giris(client)
        client.post(
            "/api/users", json={"username": "izleyen", "password": "ikinci-uzun-parola"}
        )
        kisiler = {u["username"]: u for u in client.get("/api/users").json()["users"]}
        proje = client.get("/api/projects").json()["active"]
        client.post(
            f"/api/projects/{proje['id']}/members",
            json={"user_id": kisiler["izleyen"]["id"], "role": "viewer"},
        )
        client.post("/api/auth/logout")
        self._giris(client, "izleyen", "ikinci-uzun-parola")
        return proje

    def test_the_served_workspace_is_registered_as_a_project(self, sunucu, settings):
        self._giris(sunucu)
        aktif = sunucu.get("/api/projects").json()["active"]
        assert aktif["path"] == str(settings.workspace.resolve())

    def test_every_existing_account_becomes_a_member(self, sunucu):
        """Kayit bugun calisan hicbir yetkiyi DARALTMAMALI."""
        self._giris(sunucu)
        proje = sunucu.get("/api/projects").json()["active"]
        uyeler = sunucu.get(f"/api/projects/{proje['id']}/members").json()["members"]
        assert [(u["username"], u["role"]) for u in uyeler] == [("yonetici", "owner")]

    def test_a_viewer_can_read_but_not_run(self, sunucu):
        self._izleyici_ac(sunucu)

        # Okuma serbest.
        assert sunucu.get("/api/overview").status_code == 200
        assert sunucu.get("/api/documents").status_code == 200

        # Degistirme degil.
        assert sunucu.post("/api/run", json={"phases": ["ingest"]}).status_code == 403
        assert sunucu.post("/api/ingest", json={"path": "docs"}).status_code == 403
        assert sunucu.post("/api/plans", json={"name": "yeni"}).status_code == 403

    def test_a_viewer_reads_the_member_list_but_cannot_change_it(self, sunucu):
        """Uyelik bir SIR degil: ayni projeye yazma yetkisi olan
        insanlarin listesi, o insanlarin birbirinden gizlenmesi gereken
        bir sey degil. Kapali oldugu surece "kosuyu ayse baslatti"
        satirindaki ayse'nin kim oldugunu okuyabilecegi hicbir yer
        yoktu. Uyelik VERMEK yine sahibin isi."""
        proje = self._izleyici_ac(sunucu)
        cevap = sunucu.get(f"/api/projects/{proje['id']}/members")
        assert cevap.status_code == 200, cevap.text
        assert {u["username"] for u in cevap.json()["members"]} == {
            "yonetici", "izleyen"
        }
        assert sunucu.post(
            f"/api/projects/{proje['id']}/members",
            json={"user_id": 1, "role": "owner"},
        ).status_code == 403

    def test_the_overview_tells_the_interface_which_role_it_has(self, sunucu):
        """Bir izleyiciye "Baslat" dugmesini gosterip sonra 403 dondurmek,
        dugmeyi hic gostermemekten kotudur."""
        self._izleyici_ac(sunucu)
        assert sunucu.get("/api/overview").json()["project"]["role"] == "viewer"

    def test_an_admin_reaches_a_project_they_are_not_a_member_of(self, sunucu, tmp_path):
        """Yoksa sahibi ayrilmis bir proje kimsenin ulasamadigi bir
        dizine donusurdu."""
        self._giris(sunucu)
        baska = tmp_path / "baska-proje"
        baska.mkdir()
        cevap = sunucu.post("/api/projects", json={"path": str(baska)})
        assert cevap.status_code == 200, cevap.text

        gorunen = sunucu.get("/api/projects").json()["projects"]
        assert len(gorunen) == 2


class TestProjeYalitimi:
    """Iki proje birbirini GORMEMELI ve birbirini BEKLEMEMELI.

    Bunlar cok kullanicili hedefin gercek sinavi: bugune kadar tek
    Orchestrator + tek RunManager vardi ve ikinci kosu `RunBusy` ile
    reddediliyordu -- yani A kullanicisinin kosusu B'nin isini
    engelliyordu.
    """

    @pytest.fixture
    def sunucu(self, settings, tmp_path):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            yield client

    def _ikinci_proje(self, client, tmp_path):
        yol = tmp_path / "ikinci"
        (yol / "docs").mkdir(parents=True)
        (yol / "docs" / "baska.md").write_text(
            "# Muhasebe\n\nFatura kesme ve e-arsiv.\n", encoding="utf-8"
        )
        cevap = client.post("/api/projects", json={"path": str(yol), "name": "Ikinci"})
        assert cevap.status_code == 200, cevap.text
        return cevap.json()["project"]

    def test_switching_changes_the_workspace(self, sunucu, settings, tmp_path):
        ikinci = self._ikinci_proje(sunucu, tmp_path)
        assert sunucu.get("/api/overview").json()["workspace"] == str(settings.workspace)

        assert sunucu.post(f"/api/projects/{ikinci['id']}/activate").status_code == 200
        sonra = sunucu.get("/api/overview").json()
        assert sonra["workspace"] == ikinci["path"]
        assert sonra["project"]["id"] == ikinci["id"]

    def test_documents_do_not_leak_between_projects(self, sunucu, tmp_path):
        """Iki proje ayni veritabanini paylassaydi biri otekinin
        belgelerini gorurdu."""
        ikinci = self._ikinci_proje(sunucu, tmp_path)

        sunucu.post("/api/ingest", json={"path": "docs"})
        birinci_belgeler = sunucu.get("/api/documents").json()["documents"]
        assert len(birinci_belgeler) == 1

        sunucu.post(f"/api/projects/{ikinci['id']}/activate")
        assert sunucu.get("/api/documents").json()["documents"] == []

        sunucu.post("/api/ingest", json={"path": "docs"})
        ikinci_belgeler = sunucu.get("/api/documents").json()["documents"]
        assert len(ikinci_belgeler) == 1
        assert ikinci_belgeler[0]["title"] != birinci_belgeler[0]["title"]

    def test_a_run_in_one_project_does_not_block_the_other(self, sunucu, tmp_path):
        """`RunBusy` artik PROJE kapsamli.

        Tek `RunManager` varken A kullanicisinin kosusu B'nin kosusunu
        reddediyordu; iki kullanicinin ayni anda calisamamasi, cok
        kullanicili olmanin tam tersi.
        """
        ikinci = self._ikinci_proje(sunucu, tmp_path)
        sunucu.post("/api/ingest", json={"path": "docs"})

        # Birinci projede uzun surecek bir kosu yerine, kosu kaydini
        # tutan RunManager'larin AYRI nesneler oldugunu dogruluyoruz:
        # ayni nesne olsalardi ikinci proje birincinin durumunu gorurdu.
        durum = sunucu.app.state.deerx
        birinci_rt = durum.runtime(durum.default_project)
        ikinci_rt = durum.runtime(durum.projects.get(ikinci["id"]))
        assert birinci_rt.runner is not ikinci_rt.runner
        assert birinci_rt.orchestrator is not ikinci_rt.orchestrator
        assert birinci_rt.settings.workspace != ikinci_rt.settings.workspace

    def test_a_forged_cookie_cannot_open_someone_elses_project(self, settings, tmp_path):
        """Cerez istemcide duruyor ve elle degistirilebilir; uyelik HER
        istekte dogrulanmali."""
        from starlette.testclient import TestClient

        from deerx.web.app import PROJECT_COOKIE, build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            yol = tmp_path / "gizli"
            yol.mkdir()
            gizli = client.post(
                "/api/projects", json={"path": str(yol)}
            ).json()["project"]
            client.post(
                "/api/users",
                json={"username": "yabanci", "password": "ikinci-uzun-parola"},
            )
            client.post("/api/auth/logout")
            client.post(
                "/api/auth/login",
                json={"username": "yabanci", "password": "ikinci-uzun-parola"},
            )

            client.cookies.set(PROJECT_COOKIE, str(gizli["id"]))
            # Cerez YOK SAYILIR ve istek varsayilan projeye duser; orada
            # da uyeligi olmadigi icin 403 alir. Sessizce BASKA bir
            # projenin verisini dondurmek daha kotu olurdu: paylasilan
            # bir baglanti yanlis veriyi dogru baslikla gosterirdi.
            assert client.get("/api/overview").status_code == 403
            assert client.post(
                f"/api/projects/{gizli['id']}/activate"
            ).status_code == 403


class TestPortDilimi:
    """Her projenin kendi port araligi olmali.

    Docker yayinlanan portlari konteyner YARATILIRKEN ayirir ve sonradan
    ekleyemez. Iki proje ayni araligi yayinlamaya calisirsa ikincisinin
    konteyneri hic kurulamaz -- yani dilim, kap kurulmadan ONCE ve kalici
    olarak belli olmali.
    """

    def test_each_project_gets_its_own_slice(self, store, alan):
        bir = store.create(alan("bir"), owner_id=1, port_base=8100, port_count=10)
        iki = store.create(alan("iki"), owner_id=1, port_base=8100, port_count=10)
        uc = store.create(alan("uc"), owner_id=1, port_base=8100, port_count=10)

        assert [bir.port_base, iki.port_base, uc.port_base] == [8100, 8110, 8120]
        assert {p.port_count for p in (bir, iki, uc)} == {10}

    def test_slices_never_overlap(self, store, alan):
        dilimler = []
        for i in range(5):
            p = store.create(alan(f"p{i}"), owner_id=1, port_base=8100, port_count=10)
            dilimler.append(range(p.port_base, p.port_base + p.port_count))
        for a in range(len(dilimler)):
            for b in range(a + 1, len(dilimler)):
                assert not set(dilimler[a]) & set(dilimler[b])

    def test_a_freed_slice_is_reused(self, store, alan, tmp_path):
        """Yoksa uzun omurlu bir kurulumda taban surekli yukselir ve bir
        gun ayricalikli olmayan port araligini asardi."""
        bir = store.create(alan("bir"), owner_id=1, port_base=8100, port_count=10)
        store.create(alan("iki"), owner_id=1, port_base=8100, port_count=10)

        store._conn.execute("DELETE FROM projects WHERE id = ?", (bir.id,))
        store._conn.commit()

        yeni = store.create(alan("uc"), owner_id=1, port_base=8100, port_count=10)
        assert yeni.port_base == 8100

    def test_the_runtime_uses_the_projects_slice(self, settings, tmp_path):
        """Ayar dosyasindaki taban degil, PROJENIN kaydindaki dilim
        kullanilmali."""
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            durum = client.app.state.deerx
            ikinci_yol = tmp_path / "ikinci"
            ikinci_yol.mkdir()
            ikinci = client.post(
                "/api/projects", json={"path": str(ikinci_yol)}
            ).json()["project"]

            birinci_rt = durum.runtime(durum.default_project)
            ikinci_rt = durum.runtime(durum.projects.get(ikinci["id"]))

            assert birinci_rt.settings.sandbox_port_base !=                 ikinci_rt.settings.sandbox_port_base
            assert ikinci_rt.settings.sandbox_port_base == ikinci["port_base"]

    def test_the_environment_endpoint_reports_the_slice(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            veri = client.get("/api/environment").json()
            assert veri["ports"]["base"] == veri["project"]["port_base"]
            assert veri["ports"]["last"] == (
                veri["ports"]["base"] + veri["ports"]["count"] - 1
            )
            assert "sandbox" in veri and "services" in veri


class TestOkumaDaUyelikIster:
    """OLCULEN SIZINTI: hicbir OKUMA ucu uyelik sormuyordu.

    `_require_role` yalnizca yazma uclarinda cagriliyordu. Sonuc: hicbir
    projeye uye olmayan bir hesap, sunucunun acilis projesinin hedefini,
    talimatini, bilgi tabanini, planini ve ciktilarini goruyordu. Tek
    kullanicida "makul varsayilan"di; cok kullanicida sessiz sizinti --
    ve "bu projeye erisimin yok" ekraninin bugune kadar hic
    tetiklenememesinin sebebi.
    """

    @pytest.fixture
    def yabanci(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            # Uyeligi OLMAYAN bir hesap ac.
            client.post(
                "/api/users",
                json={"username": "yabanci", "password": "ikinci-uzun-parola"},
            )
            proje = client.get("/api/projects").json()["active"]
            client.delete(f"/api/projects/{proje['id']}/members/2")
            client.post("/api/auth/logout")
            client.post(
                "/api/auth/login",
                json={"username": "yabanci", "password": "ikinci-uzun-parola"},
            )
            yield client

    @pytest.mark.parametrize("yol", [
        "/api/overview",
        "/api/documents",
        "/api/artifacts",
        "/api/state/requirements",
        "/api/runs",
    ])
    def test_a_non_member_cannot_read_the_project(self, yabanci, yol):
        cevap = yabanci.get(yol)
        assert cevap.status_code == 403, f"{yol} -> {cevap.status_code}"


class TestOnaySahipligi:
    """Ajanin calistirmak istedigi tehlikeli komutu, o kosuyu BASLATAN
    kisi degerlendirmeli.

    Bugune kadar kosuyu kimin baslattigi hicbir yerde kayitli degildi ve
    bekleyen bir onayi projedeki herhangi bir gelistirici cozebiliyordu:
    baskasi icin "evet" demek, onun adina risk almak ve gunlukte onun
    adini birakmak olurdu.
    """

    @pytest.fixture
    def iki_kisi(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            client.post(
                "/api/users",
                json={"username": "ekip", "password": "ikinci-uzun-parola"},
            )
            kisiler = {u["username"]: u for u in client.get("/api/users").json()["users"]}
            proje = client.get("/api/projects").json()["active"]
            client.post(
                f"/api/projects/{proje['id']}/members",
                json={"user_id": kisiler["ekip"]["id"], "role": "developer"},
            )
            yield client

    def test_the_run_records_who_started_it(self, iki_kisi):
        iki_kisi.post("/api/ingest", json={"path": "docs"})
        cevap = iki_kisi.post("/api/run", json={"phases": ["ingest"]})
        assert cevap.json()["run"]["started_by"] == "yonetici"

    def test_the_owner_is_the_username_not_the_display_name(self, iki_kisi):
        """Sahiplik KULLANICI ADIYLA yazilir ve KULLANICI ADIYLA dogrulanir.

        Iki taraf da ayni alani kullanmak zorunda: `resolve_approval`
        `started_by`i `_uploader(request)` ile karsilastiriyor ve o
        `username` donuyor. Bir gun buraya `display_name` yazilirsa onay
        sahipligi sessizce kirilir -- ve arayuz tarafinda tam olarak bu
        olmustu (bkz. tests/test_i18n.py::TestGorunenAdKendiKosusunuGizlemez).

        Ustteki test gorunen adi OLMAYAN bir kullaniciyla yazildigi icin
        ikisini ayirt edemiyordu.
        """
        # `display_name` icin ayri bir yazici yok; sutun dogrudan yazilir.
        auth = iki_kisi.app.state.deerx.auth
        kisi = auth.find("yonetici")
        auth._conn.execute(  # noqa: SLF001 - testin kurdugu durum
            "UPDATE users SET display_name = ? WHERE id = ?",
            ("Yönetici Hanım", kisi.id),
        )
        auth._conn.commit()  # noqa: SLF001
        assert auth.find("yonetici").display_name == "Yönetici Hanım"

        iki_kisi.post("/api/ingest", json={"path": "docs"})
        cevap = iki_kisi.post("/api/run", json={"phases": ["ingest"]})
        assert cevap.json()["run"]["started_by"] == "yonetici", (
            "sahiplik gorunen adla yazilmis; onay dogrulamasi kullanici adina "
            "bakiyor ve ikisi ayrisirsa kimse kendi onayini cozemez"
        )

    def test_another_developer_cannot_resolve_someone_elses_approval(self, iki_kisi):
        durum = iki_kisi.app.state.deerx
        calisan = durum.runtime()
        # Kosuyu "yonetici" baslatmis gibi davran ve bir onay beklet.
        import threading

        from deerx.web.runner import RunInfo

        calisan.runner._current = RunInfo(  # noqa: SLF001 - testin kurdugu durum
            id="x", phases=["ingest"], goal="", started_at=0.0, started_by="yonetici"
        )
        sonuc = []
        isci = threading.Thread(
            target=lambda: sonuc.append(
                calisan.runner._request_approval("Dosya sil", "rm -rf build")  # noqa: SLF001
            )
        )
        isci.start()
        son = __import__("time").monotonic() + 5
        while not calisan.runner.pending_approvals() and __import__("time").monotonic() < son:
            __import__("time").sleep(0.02)

        istek = iki_kisi.get("/api/approvals").json()["items"][0]

        iki_kisi.post("/api/auth/logout")
        iki_kisi.post(
            "/api/auth/login",
            json={"username": "ekip", "password": "ikinci-uzun-parola"},
        )
        cevap = iki_kisi.post(
            f"/api/approvals/{istek['id']}", json={"granted": True}
        )
        assert cevap.status_code == 403, cevap.text

        # Sahibi cozebilmeli.
        iki_kisi.post("/api/auth/logout")
        iki_kisi.post(
            "/api/auth/login",
            json={"username": "yonetici", "password": "cok-uzun-parola-1"},
        )
        assert iki_kisi.post(
            f"/api/approvals/{istek['id']}", json={"granted": False}
        ).status_code == 200
        isci.join(timeout=5)
        assert sonuc == [False]

    def test_a_non_member_cannot_even_see_the_queue(self, iki_kisi):
        """Istek metni ajanin calistirmak istedigi KOMUTU tasiyor."""
        iki_kisi.post("/api/auth/logout")
        iki_kisi.post(
            "/api/users", json={"username": "x", "password": "y"}
        )
        assert iki_kisi.get("/api/approvals").status_code == 401


class TestHesapAyariKisiye_Ozeldir:
    """"Yalnizca beni etkiler" gercekten yalnizca beni etkilemeli.

    `SettingField.scope` UC deger belgeliyor ama kalici yazici IKI kova
    kullaniyordu: "platform degilse proje". `language` -- kapsami "hesap"
    diye isaretlenmis TEK alan -- `<proje>/deerx.toml`a dusuyordu ve
    arayuzu Ingilizceye ceviren kisi AYNI PROJEYE GIREN HERKESIN
    ekranini Ingilizce yapiyordu.
    """

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            yield client

    @staticmethod
    def _giris(client, ad="yonetici", parola="cok-uzun-parola-1"):
        assert client.post(
            "/api/auth/login", json={"username": ad, "password": parola}
        ).status_code == 200

    def _ikinci_kisi(self, client):
        """Projeye gelistirici olarak ikinci bir hesap katar."""
        client.post(
            "/api/users",
            json={"username": "oteki", "password": "ikinci-uzun-parola"},
        )
        kisiler = {u["username"]: u for u in client.get("/api/users").json()["users"]}
        proje = client.get("/api/projects").json()["active"]
        assert client.post(
            f"/api/projects/{proje['id']}/members",
            json={"user_id": kisiler["oteki"]["id"], "role": "developer"},
        ).status_code == 200

    def test_an_account_setting_never_touches_the_project_file(self, sunucu, settings):
        self._giris(sunucu)
        assert sunucu.post(
            "/api/settings", json={"language": "en"}
        ).status_code == 200

        dosya = settings.workspace / "deerx.toml"
        icerik = dosya.read_text(encoding="utf-8") if dosya.is_file() else ""
        assert "language" not in icerik, (
            "hesap ayari proje dosyasina yazildi: projeye giren HERKESIN "
            "dilini degistirir"
        )

    def test_two_people_keep_two_languages(self, sunucu):
        """Ortak `Settings` nesnesinden okumak "en son kim kaydettiyse
        onun dili" demekti."""
        self._giris(sunucu)
        self._ikinci_kisi(sunucu)
        sunucu.post("/api/settings", json={"language": "en"})
        assert sunucu.get("/api/overview").json()["settings"]["language"] == "en"

        sunucu.post("/api/auth/logout")
        self._giris(sunucu, "oteki", "ikinci-uzun-parola")
        assert sunucu.get("/api/overview").json()["settings"]["language"] == "tr", (
            "baskasinin dil tercihi bu hesabin ekranina sizdi"
        )

        # Ve kendi secimi kendisinde kalir.
        sunucu.post("/api/settings", json={"language": "en"})
        sunucu.post("/api/auth/logout")
        self._giris(sunucu)
        assert sunucu.get("/api/overview").json()["settings"]["language"] == "en"

    def test_the_account_file_is_per_user(self, sunucu):
        from deerx.config import platform_home

        self._giris(sunucu)
        sunucu.post("/api/settings", json={"language": "en"})
        dosyalar = sorted((platform_home() / "users").glob("*.toml"))
        assert len(dosyalar) == 1, f"beklenen tek hesap dosyasi, bulunan {dosyalar}"
        assert "language" in dosyalar[0].read_text(encoding="utf-8")


class TestUyeListesiUyeninHakki:
    """Liste uyeye acik, uye olmayana kapali."""

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            yield client

    def test_a_non_member_still_gets_nothing(self, sunucu, tmp_path):
        """Kapiyi genisletmek onu ACMAK degil: uye olmayan biri hala
        projenin kimlerden olustugunu ogrenemez."""
        assert sunucu.post(
            "/api/auth/login",
            json={"username": "yonetici", "password": "cok-uzun-parola-1"},
        ).status_code == 200
        proje = sunucu.get("/api/projects").json()["active"]
        sunucu.post(
            "/api/users", json={"username": "yabanci", "password": "ucuncu-uzun-parola"}
        )
        sunucu.post("/api/auth/logout")
        sunucu.post(
            "/api/auth/login",
            json={"username": "yabanci", "password": "ucuncu-uzun-parola"},
        )
        assert sunucu.get(
            f"/api/projects/{proje['id']}/members",
            headers={"X-DeerX-Project": proje["slug"]},
        ).status_code == 403


class TestCaprazTaramaYazmaz:
    """Capraz proje taramasi proje veritabanlarina DOKUNMAMALI.

    "Butun islerim" ekrani N ayri SQLite dosyasi okumak zorunda, cunku her
    projenin kendi dosyasi var. Bunu `AppState.runtime()` uzerinden yapmak
    yikici olurdu ve tehlikeler somut:

    * Her acilis YAZAR -- sema gocu, yetim kosu devralma, yetim gorev
      toplama. "Sadece bakiyorum" diye acilan bir proje kosu ve gorev
      durumlarini degistirir.
    * `MAX_OPEN_PROJECTS` tahliyesi calisan bir projenin servislerini,
      tarayicisini ve konteynerini KAPATIR. Dokuzuncu projeyi tarayan bir
      ekran baskasinin ortamini soker.
    * Yonetici `_proje_uyesi`/`_project_role` uzerinden gecerse sahipsiz
      projelere kendini `owner` olarak YAZAR.

    Bu sinif ucunu de olcer.
    """

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            yield client

    @staticmethod
    def _eski_semali_proje(sunucu, tmp_path, ad="eski"):
        """`started_by` sutunu OLMAYAN bir proje veritabani tohumlar.

        Goc `ProjectState.__init__` icinde kosuyor; tarama oraya
        girmediginde bu sutun ORTAYA CIKMAMALI.
        """
        import sqlite3

        kok = tmp_path / ad
        (kok / ".deerx").mkdir(parents=True)
        db = kok / ".deerx" / "deerx.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
                " workflow_id TEXT NOT NULL DEFAULT '',"
                " title TEXT NOT NULL DEFAULT '',"
                " title_key TEXT NOT NULL DEFAULT '',"
                " title_args TEXT NOT NULL DEFAULT '{}',"
                " goal TEXT NOT NULL DEFAULT '',"
                " status TEXT NOT NULL DEFAULT 'done',"
                " cost_usd REAL NOT NULL DEFAULT 0,"
                " started_at REAL NOT NULL DEFAULT 0,"
                " finished_at REAL)"
            )
            conn.execute(
                "INSERT INTO runs (id, seq, started_at) VALUES ('k1', 1, 100.0)"
            )
        sunucu.post("/api/projects", json={"name": ad, "path": str(kok)})
        return db

    def test_the_scan_does_not_migrate_the_schema(self, sunucu, tmp_path):
        """EN KESKIN OLCUM: sutun ortaya CIKMAMALI.

        `ProjectState` uzerinden gecen bir uygulama gocu kosturur ve
        `started_by` belirir -- test duser.
        """
        import sqlite3

        db = self._eski_semali_proje(sunucu, tmp_path)
        assert sunucu.get("/api/activity/runs").status_code == 200

        with sqlite3.connect(db) as conn:
            sutunlar = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        assert "started_by" not in sutunlar, (
            "tarama sema gocu kosturdu; salt okunur olmasi gerekiyordu"
        )

    def test_a_database_without_the_column_is_all_unattributed(self, sunucu, tmp_path):
        """Sutun yoksa o projenin HER kosusu adsizdir -- dogru cevap,
        sifir yazma."""
        self._eski_semali_proje(sunucu, tmp_path)
        veri = sunucu.get("/api/activity/runs").json()
        eski = [p for p in veri["projects"] if p["name"] == "eski"][0]
        assert eski["status"] == "ok"
        assert eski["total"] == 1
        assert eski["unattributed"] == eski["total"]
        assert eski["mine"] == 0

    def test_the_scan_leaves_the_file_untouched(self, sunucu, tmp_path):
        """Ana `.db` dosyasinin boyutu ve degistirme zamani ayni kalmali.

        `-wal`/`-shm` KARSILASTIRILMAZ: salt okunur bir okuyucunun wal
        indeksine dokunmasi mesru.
        """
        db = self._eski_semali_proje(sunucu, tmp_path)
        once = (db.stat().st_size, db.stat().st_mtime_ns)
        sunucu.get("/api/activity/runs")
        assert (db.stat().st_size, db.stat().st_mtime_ns) == once

    def test_the_scan_does_not_adopt_an_ownerless_project(self, sunucu, tmp_path):
        """Bir listeyi cizmek, dokundugu her projeye uyelik satiri
        eklemek olamaz."""
        durum = sunucu.app.state.deerx
        kok = tmp_path / "sahipsiz"
        kok.mkdir()
        proje = durum.projects.create(name="Sahipsiz", path=kok, owner_id=None)
        assert durum.projects.members(proje.id) == []

        assert sunucu.get("/api/activity/runs").status_code == 200
        assert durum.projects.members(proje.id) == [], (
            "tarama sahipsiz projeyi sahiplendi"
        )

    def test_the_scan_does_not_evict_an_open_project(self, sunucu, tmp_path):
        """Tahliye baskasinin dev sunucusunu, tarayicisini ve konteynerini
        kapatir. Tarama LRU'ya HIC dokunmamali."""
        durum = sunucu.app.state.deerx
        for i in range(3):
            kok = tmp_path / f"acik{i}"
            kok.mkdir()
            proje = durum.projects.create(name=f"Acik {i}", path=kok, owner_id=1)
            durum.runtime(proje)
        once = set(durum._runtimes)  # noqa: SLF001 - testin olctugu sey
        assert once

        sunucu.get("/api/activity/runs")
        assert set(durum._runtimes) == once, (  # noqa: SLF001
            "tarama acik bir projeyi tahliye etti"
        )

    def test_a_missing_database_is_listed_not_dropped(self, sunucu, tmp_path):
        """Sessizce atlamak, kullanicinin bildigi bir projeyi yok
        gostermek ve toplami sessizce yanlis yapmak olurdu."""
        kok = tmp_path / "hic-kosulmamis"
        kok.mkdir()
        sunucu.post("/api/projects", json={"name": "Bos", "path": str(kok)})

        veri = sunucu.get("/api/activity/runs").json()
        bos = [p for p in veri["projects"] if p["name"] == "Bos"]
        assert bos, "kayitli ama hic kosulmamis proje listeden dusurulmus"
        assert bos[0]["status"] == "empty"

    def test_a_corrupt_database_does_not_break_the_response(self, sunucu, tmp_path):
        kok = tmp_path / "bozuk"
        (kok / ".deerx").mkdir(parents=True)
        (kok / ".deerx" / "deerx.db").write_bytes(b"bu bir sqlite dosyasi degil")
        sunucu.post("/api/projects", json={"name": "Bozuk", "path": str(kok)})

        cevap = sunucu.get("/api/activity/runs")
        assert cevap.status_code == 200
        veri = cevap.json()
        assert veri["unreadable"] >= 1
        bozuk = [p for p in veri["projects"] if p["name"] == "Bozuk"][0]
        assert bozuk["status"] == "unreadable"


class TestCaprazTaramaYetkisi:
    """Kim kimin isini gorebilir."""

    @pytest.fixture
    def iki_kisi(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            client.post(
                "/api/users",
                json={"username": "ekip", "password": "ikinci-uzun-parola"},
            )
            yield client

    @staticmethod
    def _gec(client, ad, parola="ikinci-uzun-parola"):
        client.post("/api/auth/logout")
        assert client.post(
            "/api/auth/login", json={"username": ad, "password": parola}
        ).status_code == 200

    def test_a_plain_user_cannot_ask_for_everyone(self, iki_kisi):
        """Sessizce `me`ye DUSURULMEZ: bir yoneticinin paylastigi baglanti,
        alan kisiye kendi verisini baskasinin adiyla gostermemeli."""
        self._gec(iki_kisi, "ekip")
        assert iki_kisi.get("/api/activity/runs?who=all").status_code == 403

    def test_a_plain_user_cannot_ask_for_someone_else(self, iki_kisi):
        self._gec(iki_kisi, "ekip")
        assert iki_kisi.get("/api/activity/runs?who=yonetici").status_code == 403

    def test_a_plain_user_can_ask_for_themselves(self, iki_kisi):
        self._gec(iki_kisi, "ekip")
        cevap = iki_kisi.get("/api/activity/runs?who=ekip")
        assert cevap.status_code == 200
        assert cevap.json()["who"] == "ekip"

    def test_an_admin_can_ask_for_everyone(self, iki_kisi):
        cevap = iki_kisi.get("/api/activity/runs?who=all")
        assert cevap.status_code == 200
        assert cevap.json()["who"] == "all"
        assert cevap.json()["can_see_everyone"] is True

    def test_a_plain_user_only_sees_their_own_projects(self, iki_kisi, tmp_path):
        """Uye olunmayan proje LISTEDE BILE gorunmemeli."""
        kok = tmp_path / "gizli"
        kok.mkdir()
        iki_kisi.post("/api/projects", json={"name": "Gizli", "path": str(kok)})

        self._gec(iki_kisi, "ekip")
        veri = iki_kisi.get("/api/activity/runs").json()
        assert "Gizli" not in [p["name"] for p in veri["projects"]]

    def test_an_unnamed_run_is_counted_but_not_claimed(self, iki_kisi):
        """`started_by=''` "bilinmiyor" demek, "ben" degil: `deerx run`
        ile terminalden baslatilan kosunun sahibi yoktur."""
        durum = iki_kisi.app.state.deerx
        calisan = durum.runtime()
        calisan.orchestrator.state.start_run(
            "adsiz", goal="h", phases=["ingest"], started_by=""
        )
        calisan.orchestrator.state.finish_run("adsiz", status="done")

        veri = iki_kisi.get("/api/activity/runs").json()
        assert "adsiz" not in [r["id"] for r in veri["runs"]], (
            "sahipsiz kosu 'benim islerim' listesine girdi"
        )
        toplam_adsiz = sum(p["unattributed"] for p in veri["projects"])
        assert toplam_adsiz >= 1, "sahipsiz kosu hic sayilmamis"


class TestCaprazIndirme:
    """Baska bir projenin ciktisini indirmek.

    Capraz liste bir ciktinin VAR oldugunu soyluyorsa, onu almanin da bir
    yolu olmali -- yoksa liste bir vitrin olur. Ama yol, taramanin
    kendisine koyulan kisidi bozmamali: salt okunur baglanti, `runtime()`
    yok, goc yok, diske hic dokunulmaz.
    """

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            client.post(
                "/api/users",
                json={"username": "ekip", "password": "ikinci-uzun-parola"},
            )
            yield client

    @staticmethod
    def _proje(sunucu, tmp_path, ad, *, kim="yonetici", icerik=b"# rapor\n", kosulu=True):
        """Kendi veritabani olan ikinci bir proje; icinde blob'lu bir cikti."""
        from deerx.pipeline.models import Artifact
        from deerx.pipeline.state import ProjectState

        kok = tmp_path / ad
        (kok / ".deerx").mkdir(parents=True)
        durum = ProjectState(kok / ".deerx" / "deerx.db")
        try:
            run_id = ""
            if kosulu:
                run_id = "k1"
                durum.start_run(run_id, goal="hedef", phases=["design"], started_by=kim)
                durum.finish_run(run_id, status="done")
            durum.add_artifact(
                Artifact(name=f"{ad}.md", kind="report", path=str(kok / f"{ad}.md")),
                run_id=run_id,
                blob=icerik,
            )
        finally:
            durum.close()
        cevap = sunucu.post("/api/projects", json={"name": ad, "path": str(kok)})
        assert cevap.status_code == 200, cevap.text
        return cevap.json()["project"]["id"], kok / ".deerx" / "deerx.db"

    @staticmethod
    def _gec(sunucu, ad, parola="ikinci-uzun-parola"):
        sunucu.post("/api/auth/logout")
        assert sunucu.post(
            "/api/auth/login", json={"username": ad, "password": parola}
        ).status_code == 200

    def test_the_listing_says_which_artifacts_can_be_downloaded(self, sunucu, tmp_path):
        """`stored` ve `bytes` SQL'den gelir; hicbir `stat()` cagrilmaz."""
        self._proje(sunucu, tmp_path, "alfa", icerik=b"x" * 40)

        veri = sunucu.get("/api/activity/artifacts").json()
        proje = [p for p in veri["projects"] if p["name"] == "alfa"][0]
        oge = proje["runs"][0]["items"][0]
        assert oge["stored"] is True
        assert oge["bytes"] == 40
        assert oge["sha256"]
        assert oge["download"].endswith("/download")
        assert str(proje["id"]) in oge["download"]

    def test_a_member_downloads_the_bytes(self, sunucu, tmp_path):
        _pid, _db = self._proje(sunucu, tmp_path, "beta", icerik=b"# beta raporu\n")
        veri = sunucu.get("/api/activity/artifacts").json()
        oge = [p for p in veri["projects"] if p["name"] == "beta"][0]["runs"][0]["items"][0]

        cevap = sunucu.get(oge["download"])
        assert cevap.status_code == 200
        assert cevap.content == b"# beta raporu\n"
        assert cevap.headers["content-type"] == "application/octet-stream"
        assert "attachment" in cevap.headers["content-disposition"]

    def test_the_download_leaves_the_other_database_untouched(self, sunucu, tmp_path):
        """Tarama gibi indirme de SALT OKUNUR: dosyanin boyutu ve
        degistirilme zamani ayni kalmali (`-wal`/`-shm` haric; okuyucunun
        paylasilan bellege yazmasi mesru)."""
        _pid, db = self._proje(sunucu, tmp_path, "gama")
        veri = sunucu.get("/api/activity/artifacts").json()
        oge = [p for p in veri["projects"] if p["name"] == "gama"][0]["runs"][0]["items"][0]

        once = (db.stat().st_size, db.stat().st_mtime_ns)
        assert sunucu.get(oge["download"]).status_code == 200
        assert (db.stat().st_size, db.stat().st_mtime_ns) == once, (
            "indirme hedef veritabanini degistirdi"
        )

    def test_a_non_member_gets_404_not_403(self, sunucu, tmp_path):
        """403 "yetkin yok" demek, projenin VAR OLDUGUNU soylemektir.
        Uye olmayan biri kimlikleri deneyerek varlik listesi cikaramamali."""
        pid, _db = self._proje(sunucu, tmp_path, "delta")
        self._gec(sunucu, "ekip")
        cevap = sunucu.get(f"/api/activity/artifacts/{pid}/delta.md/download")
        assert cevap.status_code == 404

    def test_a_runless_artifact_is_only_visible_to_the_all_scope(self, sunucu, tmp_path):
        """Kosusuz cikti kimseye atfedilemez: `me` kipinde ne listede ne
        indirmede gorunur, `all` kipinde ikisinde de gorunur."""
        pid, _db = self._proje(sunucu, tmp_path, "epsilon", kosulu=False)

        benim = sunucu.get("/api/activity/artifacts").json()
        assert "epsilon" not in [p["name"] for p in benim["projects"]]
        assert sunucu.get(
            f"/api/activity/artifacts/{pid}/epsilon.md/download"
        ).status_code == 404

        herkes = sunucu.get("/api/activity/artifacts?who=all").json()
        proje = [p for p in herkes["projects"] if p["name"] == "epsilon"][0]
        assert proje["runs"][0]["seq"] is None
        assert sunucu.get(
            f"/api/activity/artifacts/{pid}/epsilon.md/download?who=all"
        ).status_code == 200

    def test_someone_elses_artifact_is_not_downloadable_by_name(self, sunucu, tmp_path):
        """Listenin gostermedigi bir ciktiyi adres tahmin ederek almak
        mumkun olmamali: kapsam kurali iki ucta da ayni."""
        pid, _db = self._proje(sunucu, tmp_path, "zeta", kim="yonetici")
        durum = sunucu.app.state.deerx
        proje = next(p for p in durum.projects.all_projects() if p.name == "zeta")
        durum.projects.set_member(proje.id, 2, "developer")

        self._gec(sunucu, "ekip")
        assert sunucu.get(
            f"/api/activity/artifacts/{pid}/zeta.md/download"
        ).status_code == 404, "baskasinin kosusundaki cikti adla indirildi"

    def test_an_old_database_is_listed_but_not_downloadable(self, sunucu, tmp_path):
        """`artifact_blobs` tablosu OLMAYAN bir projede liste 200 doner,
        cikti `stored: false` gorunur, indirme 404 der ve tablo ORTAYA
        CIKMAZ -- tarama gibi indirme de goc kosturmaz."""
        import sqlite3

        kok = tmp_path / "eski"
        (kok / ".deerx").mkdir(parents=True)
        db = kok / ".deerx" / "deerx.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
                " workflow_id TEXT NOT NULL DEFAULT '',"
                " title TEXT NOT NULL DEFAULT '',"
                " title_key TEXT NOT NULL DEFAULT '',"
                " title_args TEXT NOT NULL DEFAULT '{}',"
                " goal TEXT NOT NULL DEFAULT '',"
                " status TEXT NOT NULL DEFAULT 'done',"
                " cost_usd REAL NOT NULL DEFAULT 0,"
                " started_at REAL NOT NULL DEFAULT 0,"
                " started_by TEXT NOT NULL DEFAULT '',"
                " finished_at REAL)"
            )
            conn.execute(
                "CREATE TABLE artifacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL"
                " UNIQUE, kind TEXT NOT NULL DEFAULT 'other', path TEXT NOT NULL,"
                " summary TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',"
                " phase TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO runs (id, seq, started_at, started_by)"
                " VALUES ('k1', 1, 100.0, 'yonetici')"
            )
            conn.execute(
                "INSERT INTO artifacts (name, path, run_id, created_at)"
                " VALUES ('eski.md', '/yok/eski.md', 'k1', 100.0)"
            )
        cevap = sunucu.post("/api/projects", json={"name": "eski", "path": str(kok)})
        pid = cevap.json()["project"]["id"]

        veri = sunucu.get("/api/activity/artifacts").json()
        oge = [p for p in veri["projects"] if p["name"] == "eski"][0]["runs"][0]["items"][0]
        assert oge["stored"] is False
        assert oge["bytes"] == 0
        assert oge["download"] == "", "saklanmamis cikti icin indirme adresi verilmis"

        assert sunucu.get(
            f"/api/activity/artifacts/{pid}/eski.md/download"
        ).status_code == 404

        with sqlite3.connect(db) as conn:
            tablolar = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "artifact_blobs" not in tablolar, (
            "capraz uc hedef veritabaninda tablo yaratti"
        )


class TestAkisDogruProjeyiDinler:
    """Canli akis YANLIS projenin olaylarini gosterebiliyordu.

    Uygulamanin geri kalani projeyi `X-DeerX-Project` BASLIGIYLA tasiyor
    ve gerekcesi kodda yaziyor: "Cerez tarayici genelidir: A sekmesinde
    proje degistiren kisi B sekmesinin sonraki istegini de tasiyordu."

    Ama `EventSource` baslik GONDEREMEZ -- web standardi izin vermiyor.
    Akis, uygulamada baslik disiplininden muaf kalan tek istekti ve
    cereze dusuyordu: iki sekme iki projede acikken ikisi de SON
    etkinlestirilen projenin olaylarini aliyordu.

    Cozum, baslik gonderemeyen istemci icin bir sorgu parametresi.
    Dogrulama degismiyor; asagidaki ikinci test bunu civiliyor.
    """

    @pytest.fixture
    def sunucu(self, settings):
        from starlette.testclient import TestClient

        from deerx.web.app import build_app

        with TestClient(build_app(settings)) as client:
            auth = client.app.state.deerx.auth
            auth.create_first_admin(
                auth.issue_setup_token(), "yonetici", "cok-uzun-parola-1"
            )
            client.post(
                "/api/auth/login",
                json={"username": "yonetici", "password": "cok-uzun-parola-1"},
            )
            yield client

    @staticmethod
    def _proje(client, alan, ad):
        cevap = client.post("/api/projects", json={"path": str(alan(ad))})
        assert cevap.status_code == 200, cevap.text
        return cevap.json()["project"]

    def test_the_query_parameter_beats_the_cookie(self, sunucu, alan):
        """SSE baglantisi baslik koyamaz; sorgu onun yerini tutmali.

        `/api/events` acilinca kapanmayan bir akis, o yuzden cozumlemeyi
        ayni ara katmandan gecen `/api/overview` ile olcuyoruz.
        """
        a = self._proje(sunucu, alan, "proje-a")
        b = self._proje(sunucu, alan, "proje-b")

        # Cerez B'yi gosteriyor...
        sunucu.post(f"/api/projects/{b['id']}/activate")
        assert sunucu.get("/api/overview").json()["project"]["slug"] == b["slug"]

        # ...ama sorgu A diyor ve A kazanmali.
        cevap = sunucu.get(f"/api/overview?project={a['slug']}")
        assert cevap.status_code == 200, cevap.text
        assert cevap.json()["project"]["slug"] == a["slug"], (
            "sorgu parametresi yok sayilip cereze dusuldu"
        )

    def test_the_header_still_wins_over_the_query(self, sunucu, alan):
        """Sira: baslik -> sorgu -> cerez. Baslik en ustte kalmali;
        sorgu yalnizca baslik GONDEREMEYEN istemci icin."""
        a = self._proje(sunucu, alan, "ust-a")
        b = self._proje(sunucu, alan, "ust-b")
        cevap = sunucu.get(
            f"/api/overview?project={b['slug']}",
            headers={"X-DeerX-Project": a["slug"]},
        )
        assert cevap.json()["project"]["slug"] == a["slug"]

    def test_the_query_is_not_a_new_door(self, sunucu, alan):
        """Parametre yeni bir YETKI yuzeyi acmamali: uyelik yine araniyor.

        Yonetici olmayan biri, uye olmadigi bir projenin slug'ini
        sorguda yollayarak o projeye gecememeli.
        """
        ozel = self._proje(sunucu, alan, "ozel-proje")

        auth = sunucu.app.state.deerx.auth
        auth.create_user("yabanci", "cok-uzun-parola-2", role="user")
        sunucu.post("/api/auth/logout")
        sunucu.post(
            "/api/auth/login",
            json={"username": "yabanci", "password": "cok-uzun-parola-2"},
        )

        cevap = sunucu.get(f"/api/overview?project={ozel['slug']}")
        # Uye olunmayan proje 0'a duser: acik proje yok demektir.
        assert cevap.status_code != 200 or (
            (cevap.json().get("project") or {}).get("slug") != ozel["slug"]
        ), "sorgu parametresi uyelik denetimini atliyor"

