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

    def test_a_viewer_cannot_hand_out_membership(self, sunucu):
        """Uyelik SAHIBIN isidir; gelistirici bile veremez."""
        proje = self._izleyici_ac(sunucu)
        assert sunucu.get(
            f"/api/projects/{proje['id']}/members"
        ).status_code == 403
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
            # Uye olmadigi icin cerez YOK SAYILIR ve varsayilana duser.
            assert client.get("/api/overview").json()["project"]["id"] != gizli["id"]
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
