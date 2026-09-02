from __future__ import annotations

from pathlib import Path

import pytest

from deerx.config import Settings
from deerx.logging import EventLog
from deerx.pipeline.state import ProjectState
from deerx.rag.knowledge import KnowledgeBase
from deerx.tools import ToolContext, build_registry

SPEC = """\
# Saha Servis Yonetim Sistemi

## 1. Amac
Teknik servis ekiplerinin is emirlerini mobil uzerinden yonetmesi.

## 2. Aktorler
- Saha teknisyeni: is emri goruntuler, durum gunceller.
- Operasyon yoneticisi: is emri atar, SLA takip eder.

## 3. Fonksiyonel gereksinimler

### 3.1 Is emri yasam dongusu
Is emri: acildi, atandi, yolda, basladi, tamamlandi, onaylandi.
Her gecis zaman damgasi ve aktor kaydi tutmali.

### 3.2 Cevrimdisi calisma
Teknisyen kapsama disinda kayit yapabilmeli, baglanti gelince senkron olmali.

## 4. Nonfonksiyonel gereksinimler
- Sistem 500 es zamanli teknisyeni desteklemeli.
- Veriler KVKK'ya uygun saklanmali.
"""


# Gercek modeli cagirabilecek her kimlik bilgisi. Bos dize ortam degiskeni
# `.env` dosyasindaki degeri EZER (olculdu): pydantic-settings'te ortam
# degiskeni dosyadan onceliklidir.
_GERCEK_KIMLIKLER = (
    "OPENAI_API_KEY", "DEERX_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "DEERX_ANTHROPIC_API_KEY",
    "SEARCH_API_KEY", "DEERX_SEARCH_API_KEY",
)


@pytest.fixture(autouse=True, scope="session")
def _gercek_modeli_kapat():
    """Suit gelistiricinin `.env` dosyasindaki anahtarlari GORMEMELI.

    OLCULDU. Depo kokune calisan bir `OPENAI_API_KEY` iceren `.env` konunca
    `tests/test_web.py::TestPlans::test_implement_only_touches_the_named_plan`
    takildi: testin kendi yorumu "Model yok" diyor ve LLM cagrisinin hemen
    dusmesine guveniyor. Anahtar gelince cagri gercekten yerel vLLM'e gitti
    ve test cikarim bitene kadar bekledi -- ayni test anahtarsiz 1.86 saniye,
    anahtarliyken 600 saniyede bitmedi.

    CI'da `.env` olmadigi icin bu orada gorunmez; yalnizca calisan bir modeli
    olan gelistiricinin makinesinde olur, ki bu projede olagan durum odur.
    Testler ortama gore farkli davranmamali.
    """
    import os

    onceki = {ad: os.environ.get(ad) for ad in _GERCEK_KIMLIKLER}
    for ad in _GERCEK_KIMLIKLER:
        os.environ[ad] = ""
    yield
    for ad, deger in onceki.items():
        if deger is None:
            os.environ.pop(ad, None)
        else:
            os.environ[ad] = deger


# Calisma alanini SABITLEYEN degiskenler. Bunlar acikken suit kullanicinin
# GERCEK calisma alanina yazar.
_ALAN_DEGISKENLERI = ("DEERX_WORKSPACE",)


@pytest.fixture(autouse=True)
def _sabitlenmis_alani_unut(monkeypatch):
    """Suit `DEERX_WORKSPACE`i GORMEMELI.

    OLCULDU, ve tehlikeliydi. `DEERX_WORKSPACE` ayarliyken tam suit
    kosuldu: `TestPasswordFromStdin` testleri `deerx user ensure`i alt
    surec olarak cagiriyor ve alt surec ortami devraliyor. Degisken
    `cwd`yi EZDIGI icin kullanicilar tmp_path yerine SABITLENMIS alana
    yazildi -- kanit alaninda `admin` ve `sarpel` hesaplari, bir `audit`
    tablosu, `artifacts/` ve `teslimat/` dizinleri olustu.

    Kullanicinin `demo` alani sabitlenmis olsaydi her `pytest` kosusu
    oraya hesap acacak ve `admin` PAROLASINI SIFIRLAYACAKTI -- testler
    parola degistiriyor ve `set_password` butun oturumlari dusuruyor.

    Koruma tek bir testte degil burada: bir sonraki test yazan kisinin
    bunu bilmesi gerekmesin.
    """
    for ad in _ALAN_DEGISKENLERI:
        monkeypatch.delenv(ad, raising=False)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "sartname.md").write_text(SPEC, encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> Settings:
    """Cevrimdisi ayarlar: hash gomme kullanir, model indirmez."""
    cfg = Settings(workspace=workspace, approval_mode="auto", anthropic_api_key="test-key")
    cfg.rag.embedding_provider = "hash"
    cfg.rag.embedding_dim = 128
    cfg.rag.chunk_tokens = 200
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def kb(settings: Settings) -> KnowledgeBase:
    base = KnowledgeBase(settings)
    yield base
    base.close()


@pytest.fixture
def state(settings: Settings) -> ProjectState:
    st = ProjectState(settings.db_path)
    yield st
    st.close()


@pytest.fixture
def ctx(settings: Settings, kb: KnowledgeBase, state: ProjectState) -> ToolContext:
    return ToolContext(settings=settings, events=EventLog(None, echo=False), kb=kb, state=state)


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
def orch_factory(settings):
    """Sessiz bir Orchestrator uretir; is akisi testleri icin."""
    from deerx.logging import EventLog
    from deerx.pipeline.orchestrator import Orchestrator

    def make():
        return Orchestrator(settings, events=EventLog(None, echo=False), stream=False)

    return make
