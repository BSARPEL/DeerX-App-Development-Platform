"""Bulunmus hatalarin geri gelmemesi icin testler.

Her test, gercekten yasanmis bir hatayi tarif eder. Docstring'ler hatanin ne
oldugunu ve neden onemli oldugunu anlatir; boylece biri kurali gevsetmek
istediginde neyi kirdigini gorur.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from deerx.config import Settings, load_settings
from deerx.llm.base import ToolOutcome
from deerx.logging import EventLog
from deerx.rag import KnowledgeBase
from deerx.tools import ToolContext, build_registry


class TestMisplacedConfig:
    """`[deerx]` basligi olmayan bir deerx.toml SESSIZCE tumden yok sayiliyordu.

    OLCULDU: bir calisma alanina elle `search_provider = "searxng"` ve
    `approval_mode = "auto"` yazdim; hicbiri uygulanmadi ve hicbir uyari
    cikmadi. Ayarlar `[deerx]` tablosunun altinda beklenir; ustelik yazim
    hatasi denetimi (`_warn_unknown_keys`) bu durumda BOS sozlukle
    calistigi icin o da susuyordu. Dosyanin tamami yok sayilirken susmak,
    tek bir yazim hatasinda konusmaktan daha kotu.
    """

    def test_settings_outside_the_deerx_table_are_reported(self, tmp_path, caplog):
        from deerx.config import load_settings

        (tmp_path / "deerx.toml").write_text(
            'search_provider = "searxng"\napproval_mode = "auto"\n', encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            ayarlar = load_settings(tmp_path)

        assert ayarlar.search_provider == "browser", "kok duzey zaten okunmuyor"
        kayit = " ".join(r.getMessage() for r in caplog.records)
        assert "[deerx]" in kayit, f"basligin eksikligi soylenmeli: {kayit}"
        assert "search_provider" in kayit and "approval_mode" in kayit

    def test_a_correct_file_says_nothing(self, tmp_path, caplog):
        """Isirma karsiti: dogru dosyada uyari cikmamali."""
        from deerx.config import load_settings

        (tmp_path / "deerx.toml").write_text(
            '[deerx]\nsearch_provider = "searxng"\n', encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            ayarlar = load_settings(tmp_path)
        assert ayarlar.search_provider == "searxng"
        assert "[deerx]" not in " ".join(r.getMessage() for r in caplog.records)


@pytest.fixture
def _dosyayi_golgeleme(monkeypatch):
    """Bu sinif `.env` DOSYASININ okunmasini sinar; ortam degiskeni sussun.

    `conftest.py` icindeki oturum kapsamli yalitim, gelistiricinin gercek
    anahtarini gormemek icin `OPENAI_API_KEY=""` koyuyor -- ve ortam
    degiskeni dosyadan onceliklidir, ki yalitim tam olarak buna dayaniyor.
    Ayni oncelik burada testin olcmek istedigi seyi de golgeliyordu.
    `monkeypatch` degiskeni test bitince geri koyar.
    """
    for ad in ("OPENAI_API_KEY", "DEERX_OPENAI_API_KEY"):
        monkeypatch.delenv(ad, raising=False)


@pytest.mark.usefixtures("_dosyayi_golgeleme")
class TestWorkspaceEnvFile:
    """`.env` gecerli dizinden degil, CALISMA ALANINDAN okunmali.

    `SettingsConfigDict(env_file=".env")` yolu gecerli dizine gore cozer.
    `deerx serve --workspace X` ya da DEERX_WORKSPACE ile calisan MCP sunucusu
    baska bir dizinden baslatildiginda projenin anahtarini sessizce gormezden
    gelirdi — belgelenmis MCP kurulumu tam da boyle calisiyor.
    """

    def test_workspace_env_is_read_from_another_cwd(self, tmp_path, monkeypatch):
        workspace = tmp_path / "proje"
        workspace.mkdir()
        (workspace / "deerx.toml").write_text("[deerx]\n", encoding="utf-8")
        (workspace / ".env").write_text(
            "OPENAI_API_KEY=calisma-alanindan\n", encoding="utf-8"
        )

        elsewhere = tmp_path / "baska"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        settings = load_settings(workspace)
        assert settings.openai_api_key == "calisma-alanindan"

    def test_workspace_env_wins_over_cwd_env(self, tmp_path, monkeypatch):
        workspace = tmp_path / "proje"
        workspace.mkdir()
        (workspace / ".env").write_text("OPENAI_API_KEY=proje\n", encoding="utf-8")

        elsewhere = tmp_path / "baska"
        elsewhere.mkdir()
        (elsewhere / ".env").write_text("OPENAI_API_KEY=yanlis\n", encoding="utf-8")
        monkeypatch.chdir(elsewhere)

        assert load_settings(workspace).openai_api_key == "proje"


class TestShellTimeout:
    """Zaman asimi surec AGACINI oldurmeli.

    `subprocess.run(shell=True, timeout=...)` yalnizca kabugu oldurur; torun
    surecler yasamaya devam edip borulari acik tutar ve `communicate()` onlari
    bekler. Olculmus sonuc: 2 saniyelik sinirla 30 saniyelik bir komut 30 saniye
    surdu — zaman asimi fiilen calismiyordu. Takilan bir test paketi butun boru
    hattini kilitlerdi.
    """

    @pytest.fixture
    def ctx(self, tmp_path):
        settings = Settings(workspace=tmp_path, approval_mode="auto")
        settings.shell.timeout_seconds = 2
        settings.ensure_dirs()
        return ToolContext(settings=settings, events=EventLog(None, echo=False))

    def test_timeout_ends_promptly(self, ctx):
        """Zaman asimi cagriyi bloke etmeden bitiriyor mu?

        Bu testin adi eskiden `test_timeout_kills_grandchildren` idi ama
        komutta hic torun yok ve olculen tek sey gecen sure. Torunun
        gercekten olduruldugu
        `test_kill_tree_spawn_flags_ile_baslatilan_torunlara_ulasir`
        icinde dogrulaniyor.
        """
        registry = build_registry()
        started = time.monotonic()
        result = registry.execute(
            "run_command",
            {"command": 'python -c "import time; time.sleep(30)"'},
            ctx,
        )
        elapsed = time.monotonic() - started

        assert result.is_error
        assert "sonlandirildi" in result.content
        # Sinir 2s; 8s payi ile agac gercekten oldurulmus sayilir.
        assert elapsed < 8, f"zaman asimi calismadi: {elapsed:.1f}s surdu"

    def test_normal_command_still_works(self, ctx):
        result = build_registry().execute("run_command", {"command": "echo merhaba"}, ctx)
        assert not result.is_error and "merhaba" in result.content

    def test_exit_code_is_preserved(self, ctx):
        result = build_registry().execute(
            "run_command", {"command": 'python -c "import sys; sys.exit(3)"'}, ctx
        )
        assert result.is_error and "exit_code: 3" in result.content


class TestAnswerParsing:
    """`@` ile baslayan bir cevap dosya yolu sanilmamali.

    `deerx answer Q-001 "@firma.com adresine gider"` komutu, `@` on ekini dosya
    yolu sayip cikis yapiyordu. Cevaplarda `@` sik gorulur (e-posta, kullanici adi).
    """

    def test_at_prefix_is_literal_text(self):
        from deerx.cli import _read_answer

        assert _read_answer("@firma.com adresine gider", None) == "@firma.com adresine gider"

    def test_explicit_file_flag_reads_file(self, tmp_path: Path):
        from deerx.cli import _read_answer

        path = tmp_path / "cevap.md"
        path.write_text("  dosyadan gelen cevap  ", encoding="utf-8")
        assert _read_answer("", path) == "dosyadan gelen cevap"

    def test_brief_still_supports_at_prefix(self, tmp_path: Path):
        """`--brief` icin `@dosya` bilinerek korunur — orada belgelenmis davranis."""
        from deerx.cli import _read_brief

        path = tmp_path / "talimat.md"
        path.write_text("mobil onceligi ver", encoding="utf-8")
        assert _read_brief(f"@{path}") == "mobil onceligi ver"


class TestUnknownConfigKeys:
    """deerx.toml icindeki yazim hatasi sessizce yutulmamali.

    `extra="ignore"` sayesinde `aproval_mode = "auto"` gibi bir yazim hatasi
    hicbir iz birakmadan yok sayiliyordu; kullanici ayarin neden ise yaramadigini
    anlayamazdi.
    """

    def test_unknown_key_is_reported(self, tmp_path: Path, caplog):
        (tmp_path / "deerx.toml").write_text(
            "[deerx]\naproval_mode = \"auto\"\n", encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            load_settings(tmp_path)
        assert "aproval_mode" in caplog.text
        # Yakin bir ad varsa onerilir.
        assert "approval_mode" in caplog.text

    def test_known_keys_are_silent(self, tmp_path: Path, caplog):
        (tmp_path / "deerx.toml").write_text(
            "[deerx]\napproval_mode = \"auto\"\nmax_iterations = 5\n", encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            load_settings(tmp_path)
        assert "taninmayan" not in caplog.text

    def test_sub_blocks_are_not_flagged(self, tmp_path: Path, caplog):
        (tmp_path / "deerx.toml").write_text(
            "[deerx]\n\n[deerx.rag]\ntop_k = 3\n\n[deerx.shell]\nenabled = false\n",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            load_settings(tmp_path)
        assert "taninmayan" not in caplog.text


class TestTurnOutputBudget:
    """Paralel arac cagrilari tek turda baglami sisirmemeli.

    Tek arac siniri yeterli degildi: model on araci ayni turda cagirinca
    on kat cikti tek seferde baglama giriyordu ve pencere tasiyordu.

    Testler sabit boyut degil, ayarlanmis butcenin katini kullanir; boylece
    butce degistiginde testin kendisi bayatlamaz.
    """

    @pytest.fixture
    def agent(self, tmp_path):
        from deerx.agents.base import Agent

        settings = Settings(workspace=tmp_path)
        settings.ensure_dirs()
        return Agent(
            role="analyst",
            system_prompt="x",
            registry=build_registry(),
            context=ToolContext(settings=settings, events=EventLog(None, echo=False)),
            client=None,  # bu test dongu calistirmiyor
            events=EventLog(None, echo=False),
            stream=False,
        )

    def test_oversized_turn_is_trimmed(self, agent):
        # Her biri butcenin yarisi kadar, on tane: toplam butcenin bes kati.
        chunk = agent.settings.max_turn_output_chars // 2
        outcomes = [
            ToolOutcome(call_id=str(i), name="grep_files", content="x" * chunk)
            for i in range(10)
        ]
        fitted = agent._fit_turn_budget(outcomes)
        total = sum(len(o.content) for o in fitted)
        budget = agent.settings.max_turn_output_chars
        assert total <= budget * 1.05, f"{total} > butce {budget}"
        assert all("kisaltildi" in o.content for o in fitted)

    def test_small_outputs_are_untouched(self, agent):
        outcomes = [
            ToolOutcome(call_id="a", name="x", content="kisa"),
            ToolOutcome(call_id="b", name="y", content="da kisa"),
        ]
        assert [o.content for o in agent._fit_turn_budget(outcomes)] == ["kisa", "da kisa"]

    def test_small_outputs_survive_alongside_a_huge_one(self, agent):
        """Kucuk cikti dokunulmadan gecmeli, buyuk olan kirpilmali."""
        huge = agent.settings.max_turn_output_chars * 2
        outcomes = [
            ToolOutcome(call_id="a", name="x", content="kisa"),
            ToolOutcome(call_id="b", name="y", content="y" * huge),
        ]
        fitted = agent._fit_turn_budget(outcomes)
        assert fitted[0].content == "kisa"
        assert len(fitted[1].content) < huge


class TestVectorCacheFreshness:
    """Ayni veritabanini paylasan ikinci bir ornek yeni parcalari gormeli.

    Web sunucusu acikken CLI ile indeksleme yapilirsa, sunucunun vektor
    onbellegi bayat kalip anlamsal aramada yeni belgeleri kaciriyordu.
    """

    def test_second_instance_sees_new_chunks(self, tmp_path: Path):
        settings = Settings(workspace=tmp_path)
        settings.rag.embedding_provider = "hash"
        settings.rag.embedding_dim = 64
        settings.ensure_dirs()

        writer = KnowledgeBase(settings)
        reader = KnowledgeBase(settings)
        try:
            writer.ingest_text("ilk belge", source="a.md", title="a.md")
            reader.search("ilk")  # okuyucu onbellegi doldurur

            writer.ingest_text("senkronizasyon kuyrugu", source="b.md", title="b.md")
            vector = reader.embedder.embed_query("senkronizasyon kuyrugu")
            assert len(reader.store.search_semantic(vector, 5)) == 2
        finally:
            writer.close()
            reader.close()


class TestTaskKeyValidation:
    """Gorev anahtari bicimi dogrulanmali.

    `update_task` anahtari yalnizca buyuk harfe cevirip kabul ediyordu; bicimsiz
    bir anahtar sessizce "bulunamadi" hatasina donusuyor, asil sorunu gizliyordu.
    """

    def test_malformed_key_is_rejected(self, ctx, registry):
        result = registry.execute("update_task", {"key": "gorev1", "status": "done"}, ctx)
        assert result.is_error and "Gecersiz anahtar" in result.content

    def test_valid_key_still_works(self, ctx, registry, state):
        from deerx.pipeline.models import Status, Task

        state.add_task(Task(key="T-001", title="x"))
        result = registry.execute("update_task", {"key": "t-001", "status": "done"}, ctx)
        assert not result.is_error
        assert state.get_task("T-001").status == Status.DONE


class TestEventLogRotation:
    """Olay gunlugu sinirsiz buyumemeli."""

    def test_large_log_is_rotated(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        path.write_bytes(b"x" * (EventLog.MAX_BYTES + 1))

        EventLog(path, echo=False)
        assert path.with_suffix(".jsonl.1").exists()
        assert not path.exists() or path.stat().st_size == 0

    def test_small_log_is_left_alone(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        path.write_text("kucuk", encoding="utf-8")
        EventLog(path, echo=False)
        assert path.read_text(encoding="utf-8") == "kucuk"
        assert not path.with_suffix(".jsonl.1").exists()


@pytest.mark.slow
def test_no_stale_process_left_after_timeout(tmp_path: Path):
    """Zaman asimindan sonra torun surec ayakta kalmamali."""
    if os.name != "nt":
        pytest.skip("surec agaci kontrolu Windows icin yazildi")

    import subprocess

    settings = Settings(workspace=tmp_path, approval_mode="auto")
    settings.shell.timeout_seconds = 2
    settings.ensure_dirs()
    ctx = ToolContext(settings=settings, events=EventLog(None, echo=False))

    marker = "deerx_timeout_probe"
    build_registry().execute(
        "run_command",
        {"command": f'python -c "import time; time.sleep(25)  # {marker}"'},
        ctx,
    )
    time.sleep(1)
    listing = subprocess.run(
        ["wmic", "process", "get", "commandline"], capture_output=True, text=True, check=False
    )
    assert marker not in (listing.stdout or ""), "zaman asimindan sonra surec hayatta kaldi"


class TestPlanMigration:
    """Plan sutunu sonradan eklendi; mevcut projeler kirilmamali.

    Ilk denemede `tasks_by_plan` indeksi sema betigine konmustu ve sema
    gecisten once kosuyor: sutunu olmayan eski bir veritabaninda acilis
    "no such column: plan_id" ile cokuyordu — yani her mevcut proje
    yukseltmede kiriliyordu.
    """

    @staticmethod
    def _legacy_db(path):
        """Plan kavramindan onceki bir gorev tablosu kurar."""
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE project (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'code',
                lane TEXT NOT NULL DEFAULT 'backend',
                deps TEXT NOT NULL DEFAULT '[]',
                files TEXT NOT NULL DEFAULT '[]',
                acceptance TEXT NOT NULL DEFAULT '',
                estimate TEXT NOT NULL DEFAULT 'M',
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT NOT NULL DEFAULT '',
                order_index INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO tasks (key, title, status, created_at, updated_at)
            VALUES ('T-001', 'Eski gorev', 'done', 0, 0),
                   ('T-002', 'Ikinci gorev', 'pending', 0, 0);
            """
        )
        conn.commit()
        conn.close()

    def test_opening_a_pre_plan_database_does_not_crash(self, tmp_path):
        from deerx.pipeline.state import ProjectState

        db = tmp_path / "eski.db"
        self._legacy_db(db)
        with ProjectState(db) as state:
            assert {t.key for t in state.list_tasks()} == {"T-001", "T-002"}

    def test_existing_tasks_are_adopted_by_the_default_plan(self, tmp_path):
        """Plansiz gorevler kaybolmamali; ana plana devredilmeli."""
        from deerx.pipeline.state import ProjectState

        db = tmp_path / "eski.db"
        self._legacy_db(db)
        with ProjectState(db) as state:
            plan_id = state.active_plan_id()
            adopted = state.list_tasks(plan_id=plan_id)
            assert {t.key for t in adopted} == {"T-001", "T-002"}
            assert state.get_plan(plan_id)["tasks_done"] == 1

    def test_the_plan_index_exists_after_migration(self, tmp_path):
        from deerx.pipeline.state import ProjectState

        db = tmp_path / "eski.db"
        self._legacy_db(db)
        with ProjectState(db) as state:
            names = {
                row["name"]
                for row in state._conn.execute("PRAGMA index_list(tasks)")
            }
            assert "tasks_by_plan" in names


# --------------------------------------------------------------------------
# Ajanin komutu orkestratoru olduremez.
# --------------------------------------------------------------------------


def test_spawn_flags_konsolu_da_ayirir() -> None:
    """Windows'ta surec grubu yetmez, konsol da ayrilmali.

    Gercek bir kosuda backend ajani alt surece `KeyboardInterrupt`
    gondermenin yolunu ararken `os.kill(pid, CTRL_BREAK_EVENT)` cagirdi ve
    sekiz saatlik boru hatti kosusu o anda oldu -- tek satir iz birakmadan.
    """
    import subprocess

    from deerx.process import spawn_flags

    if os.name != "nt":
        assert spawn_flags() == {"start_new_session": True}
        return

    bayraklar = spawn_flags()["creationflags"]
    assert bayraklar & subprocess.CREATE_NEW_PROCESS_GROUP, "grup ayrilmali"
    assert bayraklar & subprocess.CREATE_NO_WINDOW, (
        "konsol ayrilmali: konsol denetim olaylari sureci degil konsolu hedefler"
    )


@pytest.mark.skipif(os.name != "nt", reason="konsol denetim olaylari Windows'a ozgu")
@pytest.mark.slow
def test_ajanin_ctrl_break_i_orkestratoru_olduremez(tmp_path: Path) -> None:
    """Uctan uca: gercekte olan senaryo, gercek `spawn_flags()` ile.

    Uc seviye kurulur -- bu test kendi kabugunu korumak icin orkestrator
    taklidini AYRI bir konsola koyar; ajanin komutu ise orkestratorle ayni
    konsolu paylasmak UZERE `spawn_flags()` ile baslatilir. Gercek durum bu.

    Isirma kontrolu: `spawn_flags()` yalniz `CREATE_NEW_PROCESS_GROUP`
    dondurunce bu test `imza` dosyasi hic yazilmadigi icin duser.
    """
    import subprocess
    import sys

    ajan = tmp_path / "ajan.py"
    ajan.write_text(
        # Kendi grup lideri OLMAYAN bir torun kurar ve ona CTRL_BREAK yollar.
        "import os, signal, subprocess, sys, time\n"
        "torun = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(0.5)\n"
        "try:\n"
        "    os.kill(torun.pid, signal.CTRL_BREAK_EVENT)\n"
        "except Exception:\n"
        "    pass\n"
        "time.sleep(1.0)\n"
        "try:\n"
        "    torun.kill()\n"
        "except Exception:\n"
        "    pass\n",
        encoding="utf-8",
    )

    imza = tmp_path / "imza.txt"
    orkestrator = tmp_path / "ork.py"
    orkestrator.write_text(
        "import subprocess, sys, time\n"
        "sys.path.insert(0, sys.argv[3])\n"
        "from deerx.process import spawn_flags\n"
        "p = subprocess.Popen([sys.executable, sys.argv[1]],\n"
        "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                     **spawn_flags())\n"
        "try:\n"
        "    p.wait(timeout=20)\n"
        "except Exception:\n"
        "    p.kill()\n"
        "# CTRL_BREAK bize de ulastiysa buraya HIC gelemeyiz.\n"
        "time.sleep(0.5)\n"
        "open(sys.argv[2], 'w', encoding='utf-8').write('HAYATTA')\n",
        encoding="utf-8",
    )

    kaynak = str(Path(__file__).resolve().parents[1] / "src")
    surec = subprocess.Popen(
        [sys.executable, str(orkestrator), str(ajan), str(imza), kaynak],
        creationflags=subprocess.CREATE_NEW_CONSOLE,  # kendi kabugumuzu koru
    )
    try:
        surec.wait(timeout=90)
    except subprocess.TimeoutExpired:  # pragma: no cover
        surec.kill()
        pytest.fail("orkestrator taklidi zaman asimina ugradi")

    assert imza.is_file(), (
        "ajanin komutu orkestratoru oldurdu: CTRL_BREAK_EVENT paylasilan "
        "konsola ulasti"
    )


@pytest.mark.slow
def test_kill_tree_spawn_flags_ile_baslatilan_torunlara_ulasir(tmp_path: Path) -> None:
    """`kill_tree` gercekten torunu olduruyor mu?

    Bu fonksiyonun tum varlik sebebi bu: `npm run dev` gibi bir komutta
    oldurulen kabuk, gercek sunucuyu arkada yetim birakir ve port dolu kalir.
    Yine de hic testi yoktu -- yalnizca kendi docstring'i vardi.

    `spawn_flags()`e `CREATE_NO_WINDOW` eklendiginde bu yolun bozulmadigini da
    dogrular: Windows'ta `taskkill /F /T` surec tablosundaki ebeveyn zincirini
    yurur, surec grubunu ya da konsolu degil.

    Yardimci betikler AYRI dosyalara yazilir. Ilk yazisinda torun betigi
    ebeveynin icine gomulmustu; ic ice kacislar bozuldu, uretilen dosya
    sozdizimi hatasi verdi, torun hic dogmadi ve test YANLIS SEBEPLE gecti --
    `taskkill`ten `/T` cikarilinca bile gecmeye devam etti. Asagidaki kontrol
    adimi tam olarak bunu yakalamak icin var.
    """
    import subprocess
    import sys

    from deerx.process import kill_tree, spawn_flags

    torun_betigi = tmp_path / "torun.py"
    torun_betigi.write_text(
        """import sys, time
time.sleep(6)
open(sys.argv[1], "w", encoding="utf-8").write("YASIYOR")
""",
        encoding="utf-8",
    )

    ebeveyn_betigi = tmp_path / "ebeveyn.py"
    ebeveyn_betigi.write_text(
        """import subprocess, sys, time
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
time.sleep(30)
""",
        encoding="utf-8",
    )

    def baslat(hedef: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, str(ebeveyn_betigi), str(torun_betigi), str(hedef)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **spawn_flags(),  # type: ignore[arg-type]
        )

    # Kontrol: torun oldurulmezse imzayi GERCEKTEN yaziyor mu? Bu adim
    # olmadan test, torun hic dogmasa da gecerdi.
    kontrol_imza = tmp_path / "kontrol.txt"
    kontrol = baslat(kontrol_imza)
    time.sleep(9)
    kill_tree(kontrol.pid)
    assert kontrol_imza.is_file(), (
        "duzenek bozuk: torun oldurulmedigi halde imzayi yazmadi"
    )

    # Asil olcum.
    imza = tmp_path / "torun_yasiyor.txt"
    ebeveyn = baslat(imza)
    time.sleep(2.0)  # torunun kurulmasini bekle
    kill_tree(ebeveyn.pid)
    try:
        ebeveyn.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        ebeveyn.kill()
        pytest.fail("ebeveyn olmedi")

    # Torun hala yasiyorsa 6 saniye sonra imzayi yazar.
    time.sleep(7)
    assert not imza.is_file(), (
        "kill_tree toruna ulasmadi: yetim surec hayatta kaldi (gercek hayatta "
        "bu, portu tutmaya devam eden bir sunucu demek)"
    )


def test_cikis_kodu_notu_ntstatus_cevirir() -> None:
    """Ciplak `exit_code: 3221225786` modeli yanlis yere gonderiyordu.

    OLCULDU (gercek kosu, 08:25): ajanin komutu bir Ctrl+Break konsol olayiyla
    oldu; modele giden tek ipucu `exit_code: 3221225786` idi. 0xC000013A
    "konsol denetim olayiyla sonlandirildi" demek ve harness bunu biliyordu --
    ama sustu. Ayni bicim bu kod tabaninda defalarca duzeltildi.
    """
    from deerx.tools.shell import _cikis_kodu_notu

    assert "Ctrl" in _cikis_kodu_notu(3221225786)
    assert _cikis_kodu_notu(0) == "", "basarili komuta not eklenmemeli"
    assert _cikis_kodu_notu(1) == "", "siradan hata koduna uydurma not eklenmemeli"
    # Bellek hatasi da bir DERLEME hatasi degil; model ayrimi gorebilmeli.
    assert _cikis_kodu_notu(3221225477) != ""
    assert "3221225786" not in _cikis_kodu_notu(3221225786), "not sayiyi tekrarlamasin"


def test_cikis_kodu_notu_posix_sinyali_adlandirir() -> None:
    """POSIX'te `returncode` sinyalle olen surecler icin negatiftir."""
    import signal as _signal

    from deerx.tools.shell import _cikis_kodu_notu

    not_ = _cikis_kodu_notu(-int(_signal.SIGTERM))
    assert "SIGTERM" in not_, not_


@pytest.mark.skipif(
    os.name != "nt",
    reason="3221225786 (0xC000013A) bir WINDOWS durum kodu; POSIX cikis "
           "kodlarini 8 bite kirpar ve bu deger oradan gecemez",
)
def test_cikis_kodu_notu_modele_gercekten_ulasir(tmp_path: Path) -> None:
    """Not `run_command` ciktisinda gorunuyor mu?

    Yardimci fonksiyonu tek basina test etmek yeterli degil: cagri yerine
    baglanmamis olsaydi de gecerdi.

    IKI PLATFORM AYRINTISI, ikisi de CI'da olculdu:

    * Kod Windows'a ozgu. Linux ve macOS `waitpid`ten yalnizca 8 bit alir
      (3221225786 & 0xFF = 186), yani deger oralarda ASLA gorunmez. Test
      yillarca ubuntu ve macos bacaklarini kirmizi tuttu.
    * Cocuk surec `SystemExit(-1073741510)` ile cikiyor, `3221225786` ile
      degil. Ikisi ayni 32 bit; ama pozitif hali Python 3.13'ten oncesinde
      C long'a sigmiyor ve `OverflowError` ile 4294967295 donuyordu, yani
      Python 3.11 bacaklari da kirmiziydi. Isaretli hali her surumde
      dogru kodu veriyor -- 3.11.16 ve 3.13.13 uzerinde olculdu.
    """
    from deerx.config import Settings
    from deerx.logging import EventLog
    from deerx.tools import ToolContext, build_registry

    ayarlar = Settings(workspace=tmp_path, approval_mode="auto")
    ayarlar.ensure_dirs()
    ctx = ToolContext(settings=ayarlar, events=EventLog(None, echo=False))
    # Gercek bir cikis kodu sart: notun cagri yerine bagli oldugunu ancak
    # uctan uca bir calistirma gosterir.
    sonuc = build_registry().execute(
        "run_command",
        {"command": f'{sys.executable} -c "raise SystemExit(-1073741510)"'},
        ctx,
    )
    assert "3221225786" in sonuc.content
    assert "Ctrl" in sonuc.content, sonuc.content[:400]


def test_the_suite_never_sees_a_real_model_key() -> None:
    """Testler gelistiricinin `.env` dosyasindaki anahtari gormemeli.

    OLCULDU. Depo kokune calisan bir `OPENAI_API_KEY` iceren `.env` konunca
    `test_implement_only_touches_the_named_plan` takildi. O testin kendi
    yorumu "Model yok" diyor ve LLM cagrisinin hemen dusmesine guveniyor;
    anahtar gelince cagri gercekten yerel vLLM'e gitti ve test cikarim
    bitene kadar bekledi. Ayni test anahtarsiz 1.86 saniye surdu,
    anahtarliyken 600 saniyede bitmedi.

    CI'da `.env` olmadigi icin orada hic gorunmez; yalnizca calisan bir
    modeli olan gelistiricinin makinesinde olur -- ki bu projede olagan
    durum tam olarak odur. Yalitimi `conftest.py` icindeki oturum kapsamli
    `_gercek_modeli_kapat` fixture'i sagliyor.
    """
    from deerx.config import load_settings

    ayarlar = load_settings(None)
    assert not ayarlar.openai_api_key, (
        "suit gercek bir OpenAI anahtari goruyor: testler gelistiricinin "
        "modeline baglanabilir, yavaslar ve takilabilir"
    )
    assert not ayarlar.anthropic_api_key, "suit gercek bir Anthropic anahtari goruyor"


class TestTheWorkspaceCanBePinnedFromTheEnvironment:
    """`DEERX_WORKSPACE` her kapida gecerli olmali.

    Kullanici (2026-09-02) "artik her zaman demo calisma alani calissin"
    dedi. `find_workspace` YUKARI dogru yuruyor, yani depo kokunden
    calistirilan bir komut altindaki `demo/` alanini hicbir zaman
    bulamiyordu -- ve ortam degiskeni yalnizca MCP sunucusunda
    okunuyordu. Ayni degiskenin bir kapida gecerli, otekinde gecersiz
    olmasi tutarsizlikti.
    """

    @staticmethod
    def _alan(kok, ad):
        yol = kok / ad
        (yol / ".deerx").mkdir(parents=True)
        (yol / "deerx.toml").write_text('[deerx]\nlanguage = "tr"\n', encoding="utf-8")
        return yol

    def test_the_environment_variable_wins_over_the_current_directory(
        self, tmp_path, monkeypatch
    ):
        from deerx.config import find_workspace

        hedef = self._alan(tmp_path, "hedef")
        baska = self._alan(tmp_path, "baska")
        monkeypatch.chdir(baska)
        monkeypatch.setenv("DEERX_WORKSPACE", str(hedef))
        assert find_workspace() == hedef.resolve()

    def test_settings_follow_it_too(self, tmp_path, monkeypatch):
        """`find_workspace` dogru cevap verip `load_settings` baska bir
        yere bakarsa hicbir sey degismis olmaz."""
        from deerx.config import load_settings

        hedef = self._alan(tmp_path, "hedef")
        monkeypatch.chdir(self._alan(tmp_path, "baska"))
        monkeypatch.setenv("DEERX_WORKSPACE", str(hedef))
        assert load_settings().workspace == hedef.resolve()

    def test_an_explicit_path_still_wins(self, tmp_path, monkeypatch):
        """`--workspace` bayragi ortamdan daha belirli bir niyettir."""
        from deerx.config import find_workspace

        hedef = self._alan(tmp_path, "hedef")
        acik = self._alan(tmp_path, "acik")
        monkeypatch.setenv("DEERX_WORKSPACE", str(hedef))
        assert find_workspace(acik) == acik.resolve()

    def test_a_wrong_path_is_not_swallowed(self, tmp_path, monkeypatch, caplog):
        """Yazim hatasi yapan biri, komutlarinin bambaska bir yerde
        calistigini ancak veri kaybettiginde fark ederdi."""
        import logging

        from deerx.config import find_workspace

        burada = self._alan(tmp_path, "burada")
        monkeypatch.chdir(burada)
        monkeypatch.setenv("DEERX_WORKSPACE", str(tmp_path / "boyle-bir-yer-yok"))
        with caplog.at_level(logging.WARNING):
            assert find_workspace() == burada.resolve()
        assert "DEERX_WORKSPACE" in caplog.text

    def test_an_empty_value_is_ignored(self, tmp_path, monkeypatch):
        """Bos bir degisken "koke bak" demek degildir; hic verilmemis
        sayilir."""
        from deerx.config import find_workspace

        burada = self._alan(tmp_path, "burada")
        monkeypatch.chdir(burada)
        monkeypatch.setenv("DEERX_WORKSPACE", "")
        assert find_workspace() == burada.resolve()

    def test_the_suite_never_writes_into_a_pinned_workspace(self, tmp_path):
        """OLCULDU ve tehlikeliydi.

        `DEERX_WORKSPACE` ayarliyken tam suit kosuldu ve dort test
        SABITLENMIS alana yazdi: `admin` ve `sarpel` hesaplari, bir
        `audit` tablosu, `artifacts/` ve `teslimat/` dizinleri. Sebep,
        `deerx user ensure`i alt surec olarak cagiran testlerin ortami
        devretmesi ve degiskenin `cwd`yi ezmesi.

        Kullanicinin gercek alani sabitlenmis olsaydi her `pytest`
        kosusu oraya hesap acacak ve `admin` parolasini SIFIRLAYACAKTI.

        Koruma `conftest.py` icinde, autouse bir fixture: bir sonraki
        test yazanin bunu bilmesi gerekmesin. Bu test o korumanin
        yerinde durdugunu dogruluyor -- ve `monkeypatch.setenv` ile
        kurulan bir deger bile fixture tarafindan silinmis olmali.
        """
        import os

        from deerx.config import find_workspace

        assert os.environ.get("DEERX_WORKSPACE") is None, (
            "conftest korumasi kalkmis: suit sabitlenmis alana yazabilir"
        )
        # Ve cozum gercekten bulunulan dizine dusuyor.
        alan = self._alan(tmp_path, "burada")
        onceki = os.getcwd()
        os.chdir(alan)
        try:
            assert find_workspace() == alan.resolve()
        finally:
            os.chdir(onceki)
