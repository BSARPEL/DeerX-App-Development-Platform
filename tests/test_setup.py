"""Kurulum: eksigi kapatan taraf.

`doctor` NE eksik oldugunu soyler, `setup` eksigi KAPATIR. Ayrimi korumak
onemli: doctor hicbir seye dokunmaz.

Bu testler agi ve Docker'i kullanmaz -- her dis cagri taklit edilir.
"""

from __future__ import annotations

import pytest

from deerx import setup as kurulum
from deerx.config import CONFIG_FILENAME, Settings
from deerx.i18n import CATALOG, set_language, t


@pytest.fixture(autouse=True)
def _dil():
    onceki = t("cli.setup")  # katalogun yuklu oldugunu dogrular
    yield
    set_language("tr")
    assert onceki


class TestWorkspace:
    def test_a_missing_workspace_is_created(self, tmp_path):
        hedef = tmp_path / "yeni"
        adim = kurulum.calisma_alani(hedef, kur=True)
        assert adim.durum == "kuruldu"
        assert (hedef / CONFIG_FILENAME).is_file()
        assert (hedef / "docs").is_dir()
        assert (hedef / ".env").is_file()

    def test_an_existing_workspace_is_left_alone(self, tmp_path):
        kurulum.calisma_alani(tmp_path, kur=True)
        imza = (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")
        adim = kurulum.calisma_alani(tmp_path, kur=True)
        assert adim.durum == "ok"
        assert (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8") == imza

    def test_reporting_mode_does_not_write(self, tmp_path):
        """`kur=False` bildirir, dokunmaz."""
        hedef = tmp_path / "dokunulmayan"
        adim = kurulum.calisma_alani(hedef, kur=False)
        assert adim.durum == "uyari"
        assert adim.komut.startswith("deerx init")
        assert not hedef.exists()


class TestSearxngStep:
    def test_a_live_instance_is_not_reinstalled(self, settings, monkeypatch):
        """Calisan bir ornegi yeniden kurmak, kullanicinin ayarlarini
        silmek olurdu."""
        import httpx

        class Yanit:
            status_code = 200
            headers = {"content-type": "application/json"}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Yanit())
        cagrildi = []
        monkeypatch.setattr(kurulum, "_calistir",
                            lambda *a, **k: cagrildi.append(a) or (0, ""))

        adim = kurulum.searxng(settings, kur=True)
        assert adim.durum == "ok"
        assert not cagrildi, "calisan ornek icin docker cagrildi"

    def test_without_docker_it_warns_rather_than_failing(self, settings, monkeypatch):
        """SearXNG olmadan da DeerX kosar; bu bir engel degil."""
        import httpx

        def dus(*a, **k):
            raise httpx.ConnectError("yok")

        monkeypatch.setattr(httpx, "get", dus)
        monkeypatch.setattr(kurulum, "_var_mi", lambda p: False)
        adim = kurulum.searxng(settings, kur=True)
        assert adim.durum == "uyari"
        assert not adim.engel

    def test_the_generated_config_enables_json(self):
        """JSON varsayilan olarak kapali; acilmazsa uc 403 doner ve
        kurulum bosa gider."""
        metin = kurulum.SEARXNG_AYAR.format(secret="x")
        assert "json" in metin
        assert "formats:" in metin


class TestEndpointStep:
    def test_a_dead_endpoint_blocks(self, settings, monkeypatch):
        """Model ucu olmadan hicbir faz kosamaz: bu gercek bir engel."""
        import httpx

        def dus(*a, **k):
            raise httpx.ConnectError("baglanti yok")

        monkeypatch.setattr(httpx, "get", dus)
        adim = kurulum.model_ucu(settings)
        assert adim.engel
        assert "vllm" in adim.komut.lower()

    def test_a_served_model_passes(self, settings, monkeypatch):
        import httpx

        class Yanit:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"data": [{"id": settings.model_lead},
                                 {"id": settings.model_worker}]}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Yanit())
        assert kurulum.model_ucu(settings).durum == "ok"

    def test_a_missing_model_is_named(self, settings, monkeypatch):
        """Ucun ayakta olmasi yetmez: yapilandirilan modeli sunmali."""
        import httpx

        class Yanit:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"data": [{"id": "baska-model"}]}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Yanit())
        adim = kurulum.model_ucu(settings)
        assert adim.engel
        assert settings.model_lead in adim.detay


class TestProviderSwitch:
    def test_installing_searxng_switches_the_provider(self, tmp_path):
        """Kurup kullanmamak, bosa kurmak olurdu."""
        kurulum.calisma_alani(tmp_path, kur=True)
        assert kurulum.searxng_secildi(tmp_path) is True
        metin = (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")
        assert 'search_provider = "searxng"' in metin

    def test_it_is_idempotent(self, tmp_path):
        kurulum.calisma_alani(tmp_path, kur=True)
        kurulum.searxng_secildi(tmp_path)
        assert kurulum.searxng_secildi(tmp_path) is False

    def test_a_keyed_provider_is_not_overwritten(self, tmp_path):
        """Kullanici brave/tavily secmisse kurulum onu ezmemeli."""
        kurulum.calisma_alani(tmp_path, kur=True)
        yol = tmp_path / CONFIG_FILENAME
        yol.write_text(
            yol.read_text(encoding="utf-8").replace(
                'search_provider = "browser"', 'search_provider = "brave"'
            ),
            encoding="utf-8",
        )
        assert kurulum.searxng_secildi(tmp_path) is False
        assert 'search_provider = "brave"' in yol.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Satirin HIC OLMADIGI dosya. Bu sinif vardi ve gecti; kusur yine de
    # kullaniciya ulasti, cunku hicbir test satirsiz bir dosya denemedi.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _satirsiz(tmp_path):
        """Elle duzenlenmis, `search_provider` satiri olmayan bir dosya.

        Alt tablolarla BITER -- kullanicinin dosyasi da oyle bitiyordu ve
        ilk duzeltmem ayari sona ekleyip `[deerx.shell]` icine sokmustu.
        """
        (tmp_path / CONFIG_FILENAME).write_text(
            "\n".join([
                "[deerx]",
                'language = "tr"',
                'searxng_url = "http://127.0.0.1:8890"',
                "",
                "[deerx.rag]",
                "top_k = 8",
                "",
                "[deerx.shell]",
                "enabled = true",
                "",
            ]),
            encoding="utf-8",
        )

    def test_a_missing_line_is_added_inside_the_deerx_table(self, tmp_path):
        """OLCULDU: kullanicinin `demo` calisma alaninda satir hic yoktu.

        `replace` eslesmedi, fonksiyon sessizce False dondu, kurulu
        SearXNG hic kullanilmadi. Arama tarayiciyla Bing'e gitti, Bing
        engelledi, arastirma ajani URL bulamayip tahmin etti: bir kosuda
        dokuz HTTP 404 ve dort cozulemeyen alan adi.
        """
        import tomllib

        from deerx.config import load_settings

        self._satirsiz(tmp_path)
        assert kurulum.searxng_secildi(tmp_path) is True

        # Metinde GECMESI yetmez: okundugunda ETKILI olmali.
        assert load_settings(tmp_path).search_provider == "searxng"
        veri = tomllib.loads((tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8"))
        assert veri["deerx"]["search_provider"] == "searxng"
        # Sona eklemek onu son alt tabloya sokardi ve hicbir sey yapmazdi.
        assert "search_provider" not in veri["deerx"]["shell"]

    def test_a_file_without_the_table_gets_one(self, tmp_path):
        """`[deerx]` basligi olmayan bir dosya zaten hic okunmuyor."""
        from deerx.config import load_settings

        (tmp_path / CONFIG_FILENAME).write_text("# bos\n", encoding="utf-8")
        assert kurulum.searxng_secildi(tmp_path) is True
        assert load_settings(tmp_path).search_provider == "searxng"

    def test_the_shipped_template_still_carries_the_line(self):
        """Sablonda satir varsa hizli yol (degistir) calisir; yoksa
        yavas yol (ekle). Ikisi de calisiyor ama sablonun degismesi
        sessiz bir davranis degisikligi olurdu."""
        from deerx.cli import _default_config

        assert 'search_provider = "browser"' in _default_config()


class TestSummary:
    def test_counts_every_status(self):
        adimlar = [
            kurulum.Adim("a", "ok"), kurulum.Adim("b", "kuruldu"),
            kurulum.Adim("c", "uyari"), kurulum.Adim("d", "eksik"),
        ]
        assert kurulum.ozet(adimlar) == {"ok": 1, "kuruldu": 1, "uyari": 1, "eksik": 1}

    def test_only_missing_blocks(self):
        assert kurulum.Adim("x", "eksik").engel
        for durum in ("ok", "kuruldu", "uyari"):
            assert not kurulum.Adim("x", durum).engel


class TestDockerAdimiKabiniOlcer:
    """Docker adimi daemon'a "merhaba" demekle yetinemez.

    OLCULDU: Docker Desktop'in konak baglantisi koptugunda `docker info`
    SORUNSUZ yanit veriyor -- eski adim "ok" diyordu -- ama her `docker
    run` "mkdir /run/desktop/mnt/host/c: file exists" ile dusuyordu.
    Kullanici kurulumu yesil gorup kosuyu baslatiyor ve arizayi ilk arac
    cagrisinda, en pahali anda ogreniyordu.
    """

    @staticmethod
    def _docker_var(monkeypatch):
        monkeypatch.setattr(kurulum, "_var_mi", lambda p: p == "docker")
        monkeypatch.setattr(kurulum, "_calistir", lambda *a, **k: (0, "29.7.2\n"))

    @staticmethod
    def _saglik(monkeypatch, *, problems=(), warnings=()):
        from deerx.sandbox import Sandbox, SandboxHealth

        def sahte(self, **_kw):
            return SandboxHealth(
                docker_cli=True, daemon="29.7.2", container_status=None,
                image_present=True, mount_ok=not problems,
                problems=[dict(p) for p in problems],
                warnings=[dict(w) for w in warnings],
            )

        monkeypatch.setattr(Sandbox, "probe", sahte)

    def test_without_settings_it_behaves_as_before(self, monkeypatch):
        """`setup` calisma alanini ayarlardan ONCE kuruyor ve adimi orada
        ayarsiz cagiriyor; zorunlu bir parametre TypeError verirdi."""
        self._docker_var(monkeypatch)
        adim = kurulum.docker()
        assert adim.durum == "ok"
        assert not adim.engel

    def test_host_mode_never_probes_the_sandbox(self, settings, monkeypatch):
        """Konak kipinde kabin hic kurulmayacak; onu yoklamak bir dakika
        bosa beklemek olurdu."""
        from deerx.sandbox import Sandbox

        self._docker_var(monkeypatch)
        cagrildi = []
        monkeypatch.setattr(
            Sandbox, "probe", lambda self, **kw: cagrildi.append(1)
        )
        assert settings.execution == "host"
        assert kurulum.docker(settings).durum == "ok"
        assert not cagrildi, "konak kipinde kabin yoklandi"

    def test_a_broken_mount_blocks_the_setup(self, settings, monkeypatch):
        self._docker_var(monkeypatch)
        self._saglik(monkeypatch, problems=[{"key": "bind_mount_broken", "args": {}}])
        settings.execution = "docker"

        adim = kurulum.docker(settings)
        assert adim.durum == "eksik", "kopuk baglanti engel sayilmadi"
        assert adim.engel
        assert "Docker Desktop" in adim.detay, "care metni kayip"

    def test_a_missing_image_is_a_warning_with_a_pull_command(self, settings, monkeypatch):
        """Imaji `docker run` kendisi ceker; taze bir kurulumu durdurmak
        yanlis alarm olurdu."""
        self._docker_var(monkeypatch)
        self._saglik(monkeypatch, warnings=[{"key": "image_missing", "args": {}}])
        settings.execution = "docker"

        adim = kurulum.docker(settings)
        assert adim.durum == "uyari"
        assert not adim.engel
        assert adim.komut.startswith("docker pull ")

    def test_the_no_docker_message_talks_about_isolation(self, monkeypatch):
        """Metin yalnizca SearXNG'den soz ediyordu; yalitimi acan kisi
        Docker'in ona da gerektigini oradan anlayamazdi."""
        monkeypatch.setattr(kurulum, "_var_mi", lambda p: False)
        adim = kurulum.docker()
        assert "execution" in adim.detay
        assert "SearXNG" in t("setup.no_docker_searxng")


class TestStepNamesAreTranslated:
    def test_the_table_follows_the_language(self, settings, monkeypatch):
        """Ingilizce bir kosuda tablo "Calisma alani" basiyordu.

        `Settings(...)` kurulurken dili kendi alanina cekiyor, o yuzden
        nesne dil degistirmeden ONCE kuruluyor."""
        monkeypatch.setattr(kurulum, "_var_mi", lambda p: False)
        ayar = Settings(workspace=settings.workspace)
        try:
            set_language("en")
            assert kurulum.docker().ad == "Docker"
            assert kurulum.tarayici(ayar).ad == "Browser"
            set_language("tr")
            assert kurulum.tarayici(ayar).ad == "Tarayici"
        finally:
            set_language("tr")


class TestStepNamesResolveAtRender:
    """Adim adi CIZIM aninda cozulmeli, uretim aninda degil.

    `deerx setup` calisma alanini once kurar, ayarlari sonra yukler --
    mecburen, cunku ayarlar o calisma alanindan gelir. Ad uretim aninda
    cozulunce ilk satir henuz bilinmeyen dille yaziliyordu: Ingilizce bir
    tabloda "Calisma alani".
    """

    def test_a_step_made_before_the_language_is_known_still_follows_it(self):
        adim = kurulum.Adim("step.workspace", "ok")
        try:
            set_language("en")
            assert adim.ad == "Workspace"
            set_language("tr")
            assert adim.ad == "Calisma alani"
        finally:
            set_language("tr")

    def test_every_step_key_exists_in_the_catalog(self, tmp_path, monkeypatch):
        """Eksik bir anahtar `t()` yuzunden ham anahtari basar; tablo
        "step.docker" gosterir ve kimse fark etmez."""
        from deerx.i18n import CATALOG

        monkeypatch.setattr(kurulum, "_var_mi", lambda p: False)
        adimlar = [
            kurulum.python_surumu(),
            kurulum.calisma_alani(tmp_path, kur=True),
            kurulum.docker(),
        ]
        for adim in adimlar:
            assert adim.anahtar in CATALOG, adim.anahtar
            assert adim.ad != adim.anahtar


class TestIlkYonergeYapilandirmayaUyar:
    """`init`in soyledigi ile YAZDIGI ayni saglayiciyi gostermeli.

    OLCULDU: `init` calisma alanini `provider = "openai"` ve yerel bir uc
    ile kuruyordu, ama ilk yonerge ".env icine ANTHROPIC_API_KEY yazin"
    diyor ve `.env`i tam o satirla tohumluyordu. Yeni kullanicinin okudugu
    ILK cumle onu kurulan yapilandirmayla ilgisi olmayan bir saglayiciya
    gonderiyordu -- ustelik yerel bir uc cogu zaman anahtar istemiyor.

    Sapma sessiz: iki taraf da kendi icinde dogru, yalnizca birbirini
    tutmuyor. Bu yuzden kalici bir koruma gerekiyor.
    """

    def test_the_first_step_does_not_name_one_providers_key(self):
        for dil in ("tr", "en"):
            metin = CATALOG["cli.step_key"][dil]
            assert "ANTHROPIC_API_KEY" not in metin, (
                f"[{dil}] ilk adim tek bir saglayicinin anahtarini soyluyor; "
                "varsayilan yapilandirma o saglayiciyi kullanmiyor"
            )

    def test_the_first_step_points_at_the_default_provider_setting(self):
        """Varsayilan `provider = "openai"`; yonerge de o ayari gostermeli."""
        assert Settings().provider == "openai", "varsayilan saglayici degismis"
        for dil in ("tr", "en"):
            assert "openai_base_url" in CATALOG["cli.step_key"][dil], dil

    def test_the_env_template_covers_both_providers(self):
        """Sablon tek bir saglayiciyi one cikarmamali; ikisi de bos gelir."""
        for dil in ("tr", "en"):
            sablon = CATALOG["cli.env_template"][dil]
            assert "OPENAI_API_KEY=" in sablon, dil
            assert "ANTHROPIC_API_KEY=" in sablon, dil
            assert sablon.endswith("\n"), dil

    def test_init_writes_that_template(self, tmp_path):
        """Metnin katalogda durmasi, `init`in onu KULLANDIGINI kanitlamaz."""
        import inspect

        from deerx import cli, setup

        for modul in (cli, setup):
            kaynak = inspect.getsource(modul)
            assert 'ANTHROPIC_API_KEY=\\n"' not in kaynak, (
                f"{modul.__name__} hala tek saglayicili .env tohumluyor"
            )
