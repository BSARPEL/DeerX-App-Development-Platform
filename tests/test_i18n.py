"""Ceviri katmani: sozluk butunlugu ve arayuzde kacak Turkce metin kalmamasi.

Bu testlerin varlik sebebi mekaniktir: `t("bir.anahtar")` yazip sozluge
eklemeyi unutmak, ya da bir dile ekleyip digerini atlamak, kimsenin fark
etmedigi bir bosluk birakir -- arayuz o noktada ya ham anahtari gosterir ya
da yanlis dilde konusur. Insan gozune guvenmek yerine olcuyoruz.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest
from starlette.testclient import TestClient

from deerx.pipeline.models import Artifact, Phase, Status, Task
from deerx.web.app import STATIC_DIR, build_app

LANGS = ("tr", "en")


def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _node() -> str:
    """Node yoksa testi atla; JS'i ayristirmanin baska guvenilir yolu yok."""
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("node bulunamadi")
    return "node"


def _dictionaries() -> dict[str, dict[str, str]]:
    """i18n.js icindeki I18N sozlugunu Node ile degerlendirip JSON alir."""
    _node()
    script = (
        f"const fs=require('fs');"
        f"const src=fs.readFileSync({json.dumps(str(STATIC_DIR / 'i18n.js'))},'utf8');"
        # Modul degil, duz betik: sonuna bir ifade ekleyip degerini aliyoruz.
        f"const fn=new Function(src+';return I18N;');"
        f"process.stdout.write(JSON.stringify(fn()));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8"
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def i18n() -> dict[str, dict[str, str]]:
    return _dictionaries()


@pytest.fixture
def client(settings):
    app = build_app(settings)
    with TestClient(app) as test_client:
        yield test_client


class TestDictionary:
    def test_both_languages_exist(self, i18n):
        assert set(i18n) == set(LANGS)
        assert len(i18n["tr"]) > 300, "sozluk beklenmedik sekilde kucuk"

    def test_key_sets_are_identical(self, i18n):
        """Bir dile eklenip digerine eklenmeyen anahtar, o dilde bosluktur."""
        tr, en = set(i18n["tr"]), set(i18n["en"])
        assert tr - en == set(), f"ingilizcede eksik: {sorted(tr - en)}"
        assert en - tr == set(), f"turkcede eksik: {sorted(en - tr)}"

    def test_placeholders_match(self, i18n):
        """`{n}` gibi yer tutucular iki dilde ayni olmali.

        Bir dilde `{name}` yazip digerinde unutmak, o dilde ekrana ham
        `{name}` basar -- kullanicinin gordugu sey bozuk bir cumledir.
        """
        holes = {}
        for key, tr_text in i18n["tr"].items():
            a = sorted(re.findall(r"\{(\w+)\}", tr_text))
            b = sorted(re.findall(r"\{(\w+)\}", i18n["en"][key]))
            if a != b:
                holes[key] = (a, b)
        assert not holes, f"yer tutucular uyusmuyor: {holes}"

    def test_no_empty_translations(self, i18n):
        empty = [k for lang in LANGS for k, v in i18n[lang].items() if not v.strip()]
        assert not empty, f"bos ceviri: {empty}"

    def test_domain_vocabulary_is_covered(self, i18n):
        """Sunucunun urettigi her durum/faz adinin bir cevirisi olmali.

        Bunlar sunucudan ingilizce alan adi olarak gelir (`pending`,
        `implement`); sozlukte karsiligi yoksa arayuz ham degeri gosterir --
        kullanicinin sikayet ettigi tam olarak buydu.
        """
        for lang in LANGS:
            table = i18n[lang]
            for status in Status:
                assert f"status.{status}" in table, f"{lang}: status.{status} yok"
            for phase in Phase.ordered():
                for group in ("phase", "agent", "produces"):
                    assert f"{group}.{phase}" in table, f"{lang}: {group}.{phase} yok"
            for stage in {p.stage for p in Phase.ordered()}:
                assert f"stage.{stage}" in table, f"{lang}: stage.{stage} yok"
            for lane in ("backend", "frontend", "qa", "infra", "docs"):
                assert f"lane.{lane}" in table
            for priority in ("must", "should", "could", "wont"):
                assert f"priority.{priority}" in table
            for severity in ("critical", "high", "medium", "low"):
                assert f"severity.{severity}" in table


class TestUsage:
    """Kullanilan anahtarlar ile sozluk arasindaki bag."""

    def test_every_t_call_resolves(self, i18n):
        """`t("...")` icindeki her sabit anahtar sozlukte olmali."""
        js = _asset("app.js")
        # Yalnizca tam anahtarlar. `t("phase." + p.phase)` gibi birlestirmeler
        # calisma aninda kurulur; onlar asagida ayrica dogrulanir.
        used = set(re.findall(r'\bt\(\s*"([\w.]+)"\s*[,)]', js))
        assert len(used) > 100, "anahtar taramasi calismadi"
        missing = sorted(k for k in used if k not in i18n["tr"])
        assert not missing, f"sozlukte olmayan anahtar: {missing}"

    def test_composed_keys_resolve(self, i18n):
        """Birlestirilerek kurulan anahtarlarin karsiligi bulunmali."""
        js = _asset("app.js")
        prefixes = set(re.findall(r'\bt\(\s*"([\w.]+)"\s*\+', js))
        assert prefixes, "birlestirilen anahtar bulunamadi"
        for prefix in prefixes:
            assert any(k.startswith(prefix) for k in i18n["tr"]), (
                f"'{prefix}...' ile baslayan hic anahtar yok"
            )
        # Onay modu anahtarlari elle kurulur; ucu de var olmali.
        for suffix in ("Ask", "Auto", "Dry"):
            for lang in LANGS:
                assert f"nav.approval{suffix}" in i18n[lang]

    def test_every_tv_group_resolves(self, i18n):
        """`tv("grup", deger)` icin o grupta en az bir anahtar olmali."""
        js = _asset("app.js")
        groups = set(re.findall(r'\btv\(\s*"(\w+)"', js))
        assert groups, "tv() kullanimi bulunamadi"
        for group in groups:
            assert any(k.startswith(f"{group}.") for k in i18n["tr"]), \
                f"'{group}' grubunda hic anahtar yok"

    def test_html_markers_resolve(self, i18n):
        """`data-i18n*` isaretlerinin hepsi sozlukte karsilik bulmali."""
        html = _asset("index.html")
        used = set(
            re.findall(
                r'data-i18n(?:-html|-placeholder|-title|-label)?="([\w.]+)"', html
            )
        )
        assert len(used) > 80, f"cok az isaret bulundu: {len(used)}"
        missing = sorted(k for k in used if k not in i18n["tr"])
        assert not missing, f"sozlukte olmayan isaret: {missing}"

    def test_i18n_loads_before_app(self):
        """i18n.js app.js'ten once yuklenmeli; yoksa `t` tanimsizdir."""
        html = _asset("index.html")
        assert html.index("/static/i18n.js") < html.index("/static/app.js")


class TestNoStrayTurkish:
    """Arayuz kodunda sozluge girmemis Turkce metin kalmamali."""

    # Turkceye ozgu harfler: ingilizce bir metinde bulunmazlar, yani
    # varliklari "bu satir cevrilmemis" demenin en ucuz yoludur.
    TURKISH = re.compile(r"[çğıöşüÇĞİÖŞÜ]")

    def _code_lines(self, text: str) -> list[tuple[int, str]]:
        """Yorum satirlarini eler: yorumlar ceviriye tabi degil."""
        rows = []
        in_block = False
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if in_block:
                if "*/" in stripped:
                    in_block = False
                continue
            if stripped.startswith("/*"):
                in_block = "*/" not in stripped
                continue
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            rows.append((number, line))
        return rows

    def test_app_js_has_no_untranslated_text(self):
        js = _asset("app.js")
        offenders = [
            f"{n}: {line.strip()[:90]}"
            for n, line in self._code_lines(js)
            if self.TURKISH.search(line)
        ]
        assert not offenders, "app.js'de sozluge alinmamis Turkce:\n" + "\n".join(offenders)

    def test_index_html_text_is_marked(self):
        """Turkce metin tasiyan her elemanin bir `data-i18n` isareti olmali."""
        html = _asset("index.html")
        offenders = []
        for number, line in enumerate(html.splitlines(), 1):
            if not self.TURKISH.search(line):
                continue
            if line.strip().startswith("<!--"):
                continue
            # Isaret ya bu satirda ya da elemanin acildigi onceki satirlarda
            # olabilir; bloklar kisa oldugu icin +-3 satirlik pencere yeter.
            window = "\n".join(html.splitlines()[max(0, number - 4):number + 1])
            if "data-i18n" not in window:
                offenders.append(f"{number}: {line.strip()[:90]}")
        assert not offenders, "index.html'de isaretsiz Turkce:\n" + "\n".join(offenders)


class TestLanguageSwitch:
    """Dil degisiminin arayuzde gercekten uygulanmasi."""

    def test_language_setting_is_writable(self, client):
        """Dil ayari sunucuda saklanmali; yoksa yenilemede geri doner."""
        assert client.post("/api/settings", json={"language": "en"}).status_code == 200
        assert client.get("/api/overview").json()["settings"]["language"] == "en"

    def test_language_is_validated(self, client):
        response = client.post("/api/settings", json={"language": "klingon"})
        assert response.status_code == 400

    def test_local_choice_survives_a_poll(self):
        """Kaydedilmemis dil secimi bir sonraki yoklamada geri alinmamali.

        `loadOverview` her iki saniyede bir sunucunun ayarlarini getirir.
        Karsilastirma arayuzun o anki diliyle yapilsaydi, kullanici dili
        secer secmez yoklama onu eski dile dondururdu -- ayarlar ekraninda
        secim yapmak imkansiz hale gelirdi.
        """
        js = _asset("app.js")
        assert "state.serverLanguage" in js, "sunucunun onceki dili izlenmiyor"
        assert re.search(r"serverLang\s*!==\s*state\.serverLanguage", js), \
            "karsilastirma sunucunun onceki degeriyle yapilmiyor"

    def test_switch_redraws_static_text(self):
        """Dil degisince `data-i18n` metinleri yeniden cizilmeli.

        Iki giris noktasi var -- ust bardaki anahtar ve ayarlar ekranindaki
        secim -- ve ikisi de `changeLanguage` uzerinden gecer. Test o ortak
        yolun cizimi yaptigini dogrular; cagri yerlerinin kendisini degil.
        """
        js = _asset("app.js")
        blok = js[js.index("function applyLanguageLocally("):][:900]
        assert "setLanguage(" in blok
        assert "applyTranslations()" in blok

    def test_both_entry_points_go_through_one_function(self):
        """Ust bardaki anahtar ile ayarlar secimi ayrilirsa, biri sunucuya
        yazar digeri yazmaz ve fark kullaniciya gorunmez olur."""
        js = _asset("app.js")
        for cagri in ('$("#set-language").addEventListener',
                      "#lang-switch .lang-opt"):
            blok = js[js.index(cagri):][:400]
            assert "changeLanguage(" in blok, cagri

    def test_the_change_reaches_the_server(self):
        """Yalnizca arayuzu cevirmek yarim bir gecistir: olay akisi, arac
        hatalari ve ajan yonergeleri sunucudan gelir."""
        js = _asset("app.js")
        blok = js[js.index("async function changeLanguage("):][:800]
        assert '"/api/settings", { language:' in blok
        # Sunucu reddederse arayuz de donmeli; aksi halde ekran sunucuyla
        # uyusmayan bir dilde kalirdi.
        assert "applyLanguageLocally(previous)" in blok

    def test_dates_follow_the_language(self):
        """Tarih bicimi de dile uymali; sabit `tr-TR` ingilizcede yanlistir."""
        js = _asset("app.js")
        assert 'toLocaleString("tr-TR")' not in js
        assert 'toLocaleString(t("app.locale"))' in js


class TestRunIdentity:
    """Kosunun ne oldugunu listede gorebilmek.

    Kullanicinin sikayeti sudur: plan ekranindan bir gorevi baslatinca kosu
    listesinde beliriyor ama hepsi ayni yaziyor -- her satirda projenin
    hedefi vardi, hangi kosunun ne yaptigi degil.
    """

    def test_single_task_run_names_the_task(self, client, settings):
        project = client.app.state.deerx.orchestrator.state
        project.set_meta("goal", "Proje hedefi")
        project.add_task(
            Task(key="T-001", title="Saglik ucu", lane="backend", status=Status.PENDING)
        )
        response = client.post("/api/run", json={"phase": "implement", "task_key": "T-001"})
        assert response.status_code == 200
        run = response.json()["run"]
        assert "T-001" in run["title"] and "Saglik ucu" in run["title"], run["title"]
        assert run["title"] != run["goal"], "baslik hedefin kopyasi olmamali"

    def test_plan_run_names_the_plan(self, client):
        project = client.app.state.deerx.orchestrator.state
        plan_id = project.create_plan("Mobil dalga")["id"]
        response = client.post(
            "/api/run", json={"phases": ["implement"], "plan_id": plan_id}
        )
        assert response.status_code == 200
        assert "Mobil dalga" in response.json()["run"]["title"]

    def test_run_number_is_known_immediately(self, client):
        """POST yaniti kosu numarasini tasimali.

        Numara arka planda atansaydi yanit `seq: 0` donerdi ve arayuz yeni
        kosuya baglanti veremezdi -- kullanici koşuyu listede elle aramak
        zorunda kalirdi.
        """
        first = client.post("/api/run", json={"phases": ["ingest"]}).json()["run"]
        assert first["seq"] >= 1, first
        client.post("/api/run/stop")

    def test_run_appears_in_the_list_with_its_title(self, client):
        project = client.app.state.deerx.orchestrator.state
        project.add_task(Task(key="T-009", title="Kuyruk isleyici", lane="backend"))
        client.post("/api/run", json={"phase": "implement", "task_key": "T-009"})
        runs = client.get("/api/runs").json()["runs"]
        assert runs, "kosu listede yok"
        assert any("T-009" in (r["title"] or "") for r in runs), \
            [r["title"] for r in runs]

    def test_artifact_groups_carry_the_title(self, client, settings):
        project = client.app.state.deerx.orchestrator.state
        seq = project.start_run("run-abc", goal="hedef", title="T-002 · Rapor")
        assert seq >= 1
        path = settings.artifacts_dir / "rapor.md"
        path.write_text("# rapor", encoding="utf-8")
        project.add_artifact(
            Artifact(name="rapor.md", kind="report", path=str(path), run_id="run-abc")
        )
        groups = client.get("/api/artifacts").json()["groups"]
        assert any(g["title"] == "T-002 · Rapor" for g in groups), groups


def _sahibi_oldur(state, run_id: str) -> None:
    """Kaydin sahipligini, kesinlikle calismayan bir surece devreder."""
    from deerx.process import process_alive

    olu = next(
        (aday for aday in range(600000, 600200) if not process_alive(aday)),
        None,
    )
    assert olu is not None, "olu bir pid bulunamadi"
    state._conn.execute("UPDATE runs SET pid = ? WHERE id = ?", (olu, run_id))
    state._conn.commit()


class TestOrphanedRuns:
    """Sunucu yeniden baslatilinca yarida kalan kosular."""

    def test_running_runs_are_reclaimed_on_start(self, state):
        """Sahibi OLMUS bir kosu geri alinir.

        Surec olduyse o kosuyu bitirecek kimse kalmaz; kayit sonsuza dek
        "calisiyor" gorunur ve kosu listesi yalan soyler. Kullanicinin
        demo projesinde #2 iki saattir bu haldeydi.

        Olcut "acilista `running` goren her kayit" DEGIL -- o kural ayni
        calisma alanini ikinci bir surec actiginda calisan bir kosuyu
        kapatiyordu (bkz. `TestCalisanKosuYetimSanilmaz`). Bu yuzden test
        sahipligi acikca olu bir surece devrediyor.
        """
        state.start_run("dead-run", goal="yarida kalan")
        state.start_run_step("dead-run", Phase.IMPLEMENT, 0)
        _sahibi_oldur(state, "dead-run")

        reclaimed = state.reclaim_orphaned_runs()

        assert reclaimed == [state.get_run("dead-run")["seq"]]
        assert state.get_run("dead-run")["status"] == Status.CANCELLED
        assert state.get_run("dead-run")["error"], "neden durduğu yazmali"
        steps = state.run_step_rows("dead-run")
        assert all(s["status"] == Status.CANCELLED for s in steps), steps

    def test_finished_runs_are_left_alone(self, state):
        state.start_run("good-run")
        state.finish_run("good-run", status=Status.DONE)
        assert state.reclaim_orphaned_runs() == []
        assert state.get_run("good-run")["status"] == Status.DONE

    def test_reclaim_is_idempotent(self, state):
        state.start_run("dead-again")
        _sahibi_oldur(state, "dead-again")
        assert state.reclaim_orphaned_runs()
        assert state.reclaim_orphaned_runs() == []

    def test_reserved_run_keeps_its_number(self, state):
        """Ayrilmis bir kosu boru hattinda tekrar acilinca numarasi degismemeli.

        Web arayuzu numarayi hemen gosterebilmek icin kaydi kendisi acar;
        ardindan boru hatti ayni kimlikle `start_run`'a doner. Ikinci cagri
        yeni numara uretseydi kullanicinin gordugu numara kayardi.
        """
        first = state.start_run("same-id", goal="a", title="ilk")
        second = state.start_run("same-id", goal="b", title="ikinci")
        assert first == second
        run = state.get_run("same-id")
        assert run["seq"] == first
        assert run["title"] == "ikinci", "yeni bilgi kayda islenmeli"


class TestOverviewLayout:
    """Genel bakisin olculebilir duzen kusurlari.

    Hepsi tarayicida olculdu: serit kutuya sigmiyordu, kartlar 6+2 diye
    kiriliyordu, iki sutun 609'a karsi 278 pikseldi.
    """

    @staticmethod
    def _css() -> str:
        return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    @staticmethod
    def _js() -> str:
        return (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_rail_columns_follow_the_phase_count(self):
        """Serit faz sayisi kadar sutunlu izgara olmali.

        Esnek kutu + `min-width` ile on uc faz 1020 piksele cikiyor, 980
        piksellik kutuya sigmiyordu: son adim ("Canli") her zaman yatay
        kaydirmanin arkasindaydi -- boru hattinin bitisi hic gorunmuyordu.
        """
        css = self._css()
        rail = css[css.index(".phase-rail {"):]
        rail = rail[:rail.index("}")]
        assert "display: grid" in rail
        assert "repeat(var(--phases" in rail, "sutun sayisi faz sayisina bagli degil"
        assert "overflow-x: auto" not in rail, "genis ekranda kaydirma kalmamali"

    def test_script_supplies_the_phase_count(self):
        js = self._js()
        assert 'setProperty("--phases"' in js, "--phases hic yazilmiyor"

    def test_rail_is_grouped_by_stage(self):
        """On uc esit nokta yerine dort okunur obek."""
        js = self._js()
        assert "phase-stage" in js
        assert "grid-column: span" in js, "asama basliklari sutun kaplamiyor"

    def test_stage_spans_cover_every_phase(self):
        """Asama basliklarinin kapladigi sutun toplami faz sayisina esit olmali.

        Aksi halde ikinci satir kayar ve her nokta yanlis basligin altina
        duser -- sessizce yanlis bilgi veren bir duzen.
        """
        stages: dict[str, int] = {}
        for phase in Phase.ordered():
            stages[phase.stage] = stages.get(phase.stage, 0) + 1
        assert sum(stages.values()) == len(Phase.ordered())
        assert len(stages) == 4, stages

    def test_stat_grid_divides_evenly(self):
        """Sekiz kart dorde bolunur; `auto-fit` ragged 6+2 uretiyordu."""
        css = self._css()
        block = css[css.index(".grid-stats {"):]
        block = block[:block.index("}")]
        assert "repeat(4," in block, block
        assert "auto-fit" not in block

    def test_overview_columns_stretch_together(self):
        """Iki sutun ayni yuksekligi paylasmali."""
        css = self._css()
        block = css[css.index("#view-overview .panel-row {"):]
        block = block[:block.index("}")]
        assert "align-items: stretch" in block

    def test_narrow_overrides_come_after_the_base_rules(self):
        """Dar ekran kurallari genel bakis kurallarindan SONRA gelmeli.

        `#view-overview .panel-row` iki yerde ayni ozgullukte tanimliysa
        kaynak sirasi karar verir. Dar ekran kurali once yazildiginda
        telefonda iki sutun kaliyor ve paneller 125 piksele siginiyordu.
        """
        css = self._css()
        base = css.index("#view-overview .panel-row {")
        narrow = css.index("#view-overview .panel-row {", base + 1)
        assert narrow > base
        # Ikincisi gercekten bir medya sorgusunun icinde mi?
        assert "@media" in css[base:narrow], "dar ekran kurali medya sorgusunda degil"
        assert "grid-template-columns: 1fr" in css[narrow:narrow + 200]


class TestPagination:
    """Uzun listeler tek DOM'a basilmaz.

    Kirk gorev ve yirmi yedi belge tek seferde ciziliyordu: hem yavas hem
    okunmaz. Analiz ve olay akisinda zaten olan sayfalama plan ve bilgi
    tabanina da gecti.
    """

    @staticmethod
    def _js() -> str:
        return (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    @pytest.mark.parametrize("field", ["taskSize", "docSize", "analysisSize"])
    def test_default_page_size_is_25(self, field):
        js = self._js()
        assert re.search(rf"{field}:\s*25\b", js), field

    @pytest.mark.parametrize("pager", ["task-pager", "doc-pager"])
    def test_pager_element_exists(self, pager):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        assert f'id="{pager}"' in html

    @pytest.mark.parametrize("fn", ["renderTaskPage", "renderDocPage"])
    def test_render_is_split_from_fetch(self, fn):
        """Sayfa degistirmek yeniden veri cekmemeli."""
        js = self._js()
        assert f"function {fn}(" in js
        block = js[js.index(f"function {fn}("):]
        block = block[:block.index("\nasync function ") if "\nasync function " in block
                      else len(block)]
        assert "slicePage(" in block
        assert "renderPager(" in block

    def test_filters_reset_to_the_first_page(self):
        """Dorduncu sayfadayken filtrelemek bos ekran vermemeli.

        Kullanici bos ekrani "sonuc yok" sanar; oysa sonuclar birinci
        sayfadadir.
        """
        js = self._js()
        block = js[js.index('$("#task-filters").addEventListener'):]
        block = block[:block.index("});")]
        assert "state.taskPage = 1" in block

    def test_summary_counts_the_whole_list(self):
        """Ozet satiri sayfaya degil butun listeye bakmali.

        "40 gorevden 3'u tamam" bilgisi sayfa degistikce degismemeli.
        """
        js = self._js()
        block = js[js.index("function renderTaskPage("):]
        block = block[:block.index("renderPager(")]
        assert 'total: all.length' in block

    def test_an_empty_plan_hides_its_filters(self):
        """On bir filtre dugmesi bos bir listenin ustunde gurultudur.

        Hicbiri bir sey yapmaz. Suzgeclerin GORUNMESI gereken tek durum,
        suzulecek gorev olmasidir -- suzgecin sonucu bos ciktiginda
        kalirlar, yoksa geri donulemezdi.
        """
        js = self._js()
        block = js[js.index("function renderTaskPage("):]
        block = block[:block.index("renderPager(")]
        assert '$(".plan-toolbar").hidden = !all.length' in block


class TestOverviewPanelHeight:
    """Genel bakis bir PANO; uzun metin okuma yeri degil."""

    @staticmethod
    def _blocks(selector: str) -> list[str]:
        """Bir secicinin TUM govdeleri.

        Ayni secici hem temel kuralda hem dar ekran sorgusunda geciyor;
        ilkini almak medya sorgusundakini yakalayabilir.
        """
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        found = []
        start = 0
        while True:
            index = css.find(selector + " {", start)
            if index < 0:
                return found
            end = css.index("}", index)
            found.append(css[index:end])
            start = end

    def test_panels_are_capped(self):
        """Ozet paneli 560 pikselken sayfa bin pikseli asiyordu.

        Ustteki faz seridi ve istatistikler ekrandan kayiyordu; oysa panonun
        isi tam olarak onlari bir bakista gostermek.
        """
        blocks = self._blocks("#view-overview .panel-row .panel")
        assert blocks, "kural bulunamadi"
        assert any("max-height" in b for b in blocks)

    def test_summary_scrolls(self):
        blocks = self._blocks(".panel-summary .panel-body")
        assert any("overflow-y: auto" in b for b in blocks)
        assert not any("max-height: 560px" in b for b in blocks),             "sabit yukseklik panele tasinmaliydi"


class TestSettingsNeverFailsSilently:
    """Ayarlar ekrani bombos aciliyordu ve sebebini soylemiyordu.

    `loadOverview()` hatayi yutup `undefined` donuyor, cagiran taraf ise
    `loadOverview().then(renderSettings)` diyordu: istek DUSSE BILE
    `renderSettings` calisiyor, veri olmadigi icin ilk satirda sessizce
    cikiyor ve form bos kaliyordu. Tek isaret dort saniyede kaybolan bir
    toast'ti. Kullanicinin gordugu buydu.
    """

    @staticmethod
    def _js() -> str:
        return _asset("app.js")

    def test_the_screen_says_why_it_is_empty(self):
        html = _asset("index.html")
        assert 'id="settings-unavailable"' in html, "uyari satiri yok"
        blok = self._js()
        blok = blok[blok.index("function renderSettings("):]
        blok = blok[:blok.index(chr(10) + "}")]
        assert "settings-unavailable" in blok, "renderSettings uyariyi yazmiyor"
        assert "settings.unavailable" in blok, "mesaj katalogdan gelmiyor"

    @pytest.mark.parametrize("dil", LANGS)
    def test_the_message_is_translated(self, i18n, dil):
        assert "settings.unavailable" in i18n[dil]
        assert "{msg}" in i18n[dil]["settings.unavailable"], "sebep yazilmiyor"

    def test_load_overview_reports_failure(self):
        """Basari/basarisizlik DONMELI; yoksa cagiran taraf ayirt edemez."""
        js = self._js()
        blok = js[js.index("async function loadOverview("):]
        blok = blok[:blok.index(chr(10) + "}")]
        assert "return false" in blok, "dusen istek false donmuyor"
        assert "return true" in blok, "basarili istek true donmuyor"
        assert "state.overviewError" in blok, "sebep saklanmiyor"

    def test_the_isolation_panel_hides_when_the_server_does_not_know_it(self):
        """Eski bir sunucuya karsi panel sekiz BOS kutu olarak duruyordu.

        Bos kutu, ayarin var olup tanimsiz oldugunu dusundurur. Sunucu bu
        ayari hic bildirmiyorsa panel gorunmemeli.
        """
        js = self._js()
        blok = js[js.index("function yalitimiGoster("):]
        blok = blok[:blok.index(chr(10) + "}" + chr(10))]
        atama = [
            satir for satir in blok.splitlines() if "panel.hidden" in satir
        ]
        assert atama, "panel hic gizlenmiyor"
        # Gizleme KOSULU degere bagli olmali. `panel.hidden = false` de
        # "panel.hidden" iceriyor; sadece varligina bakmak bunu yakalamaz.
        assert all("kip" in satir for satir in atama), (
            f"gizleme degere bagli degil: {atama}"
        )


class TestArtifactLayout:
    """Ciktilar: tam genislikte satirlar, detay satirin altinda.

    Onceki duzen 260px'lik bir liste + ekran boyu bir yan panel idi ve
    kullanicinin sikayeti buydu: dosya adlari sikisiyor, detay tek bir
    dosya icin yarim ekran tutuyordu. Buradaki testler yeni duzeni
    korurken ESKI duzenin cozdugu problemi de koruyor -- uzun bir rapor
    sayfayi 13.600 piksele cikarmamali.
    """

    @staticmethod
    def _rule(selector: str) -> str:
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        start = css.index(selector + " {")
        return css[start:css.index("}", start)]

    def test_the_detail_does_not_stretch_the_page(self):
        """Uzun bir rapor kendi kutusunda kalmali.

        Iki sutunlu duzende bunu sutun yuksekligi engelliyordu. Tek
        sutunda tavani detayin kendisi tasimak zorunda: yoksa ustteki
        satirlar ekrandan cikar ve baska bir ciktiya gecmek icin en basa
        donmek gerekir -- duzeltilen hata tam olarak buydu.
        """
        rule = self._rule(".artifact-view")
        assert "max-height" in rule, rule
        assert "overflow-y: auto" in rule, rule

    def test_rows_span_the_full_width(self):
        """Dugmeler blok bir kapsayicida icerige gore boyutlanir.

        `width: 100%` olmadan "satir" 331px'lik bir kutu olarak kaliyor ve
        olcu bilgisi adin hemen dibinde duruyordu -- olculdu.
        """
        rule = self._rule(".artifact-item")
        assert "width: 100%" in rule, rule
        assert "grid-template-columns: minmax(0, 1fr) auto" in rule, rule

    def test_the_layout_is_a_single_column(self):
        """Yan panel geri gelmemeli."""
        rule = self._rule(".artifact-layout")
        assert "display: block" in rule, rule
        assert "grid-template-columns" not in rule, rule


class TestDuplicateKeys:
    """Ayni anahtar iki kez tanimlanmamali.

    JavaScript nesne sabitinde yinelenen anahtar hata vermez: SONUNCUSU
    kazanir, oncekiler sessizce olur. Bu oturumda tam olarak oldu -- durum
    satiri icin `settings.searchBrowser` eklendi, oysa ayni ad acilir liste
    etiketi olarak zaten kullaniliyordu; iki tanim yan yana durdu ve
    hangisinin kazandigi yalnizca siraya bagli kaldi.

    `app.language` da zaten iki kez tanimliydi (degerleri ayni oldugu icin
    kimse fark etmemisti). Biri duzenlenirse digeri sessizce ezerdi.
    """

    @staticmethod
    def _blok(js: str, dil: str) -> str:
        import re

        m = re.search(rf"^\s*{dil}:\s*\{{", js, re.M)
        assert m, f"{dil} blogu bulunamadi"
        derinlik = 0
        for j in range(m.end() - 1, len(js)):
            if js[j] == "{":
                derinlik += 1
            elif js[j] == "}":
                derinlik -= 1
                if derinlik == 0:
                    return js[m.end() : j]
        raise AssertionError(f"{dil} blogu kapanmiyor")

    @pytest.mark.parametrize("dil", ["tr", "en"])
    def test_no_key_is_defined_twice(self, dil):
        import re
        from collections import Counter

        js = _asset("i18n.js")
        anahtarlar = re.findall(r'^\s*"([\w.]+)"\s*:', self._blok(js, dil), re.M)
        assert len(anahtarlar) > 400, "anahtar taramasi calismadi"
        yinelenen = sorted(k for k, n in Counter(anahtarlar).items() if n > 1)
        assert not yinelenen, (
            f"{dil} blogunda iki kez tanimli anahtar: {yinelenen}. "
            "Sonuncusu kazanir, oncekiler sessizce olur."
        )


class TestSingularForms:
    """Ingilizce arayuz "1 chunks" diyordu.

    Sayidan sonra cogul eki Turkcede yok, Ingilizcede var. Projenin kurali
    zaten vardi (`questions.count` / `questions.countOne`); eksik olan
    sayilarin gorundugu obur yerlerde uygulanmasiydi.
    """

    TEKILLER = {
        "stat.chunkOne": "stat.chunks",
        "runs.stepOne": "runs.steps",
        "artifacts.fileOne": "artifacts.files",
        "questions.countOne": "questions.count",
    }

    @pytest.mark.parametrize("dil", LANGS)
    def test_every_singular_has_a_plural(self, i18n, dil):
        """Yalniz basina bir tekil girdi hicbir zaman kullanilmaz."""
        for tekil, cogul in self.TEKILLER.items():
            assert tekil in i18n[dil], f"{dil}: {tekil} yok"
            assert cogul in i18n[dil], f"{dil}: {cogul} yok"

    def test_english_actually_drops_the_s(self):
        js = _dictionaries()["en"]
        for tekil, cogul in self.TEKILLER.items():
            if "{n}" not in js[cogul]:
                continue
            assert js[tekil] != js[cogul], f"{tekil} cogulla ayni: {js[tekil]!r}"
            assert not js[tekil].rstrip(".").endswith("s"), js[tekil]

    @pytest.mark.parametrize(
        "tekil,cogul",
        [("stat.chunkOne", "stat.chunks"),
         ("runs.stepOne", "runs.steps"),
         ("artifacts.fileOne", "artifacts.files")],
    )
    def test_no_call_site_forgets_the_singular(self, tekil, cogul):
        """Cogul anahtar TEK BASINA cagrilmamali.

        "En az bir yerde tekil kullanilmis" yetmez: `stat.chunks` iki
        yerde geciyordu ve birinde tekil karsilik unutulsa test yine
        yesil kalirdi. Bu yuzden cogulun kosulsuz cagrisi hic olmamali.
        """
        app = _asset("app.js")
        assert f'=== 1 ? "{tekil}"' in app, f"{tekil} hic kullanilmiyor"
        assert f't("{cogul}"' not in app, (
            f"{cogul} tekil karsiligi secilmeden cagriliyor"
        )


class TestEveryKindHasAWord:
    """`tv("kind", x)` sozlukte karsiligi yoksa HAM DEGERI gosterir.

    Sessizce. Mevcut `test_every_tv_group_resolves` yalnizca grupta en az
    bir anahtar ariyordu, o yuzden `kind.screenshot` ve `kind.image`
    eksikken de yesildi -- ekranda ise ajanin cektigi ekran goruntusunun
    turu "screenshot" diye Ingilizce yaziyordu. Kodun URETEBILECEGI her
    tur burada tek tek aranir.
    """

    KAYNAK = STATIC_DIR.parent.parent

    def _kodun_urettigi_turler(self) -> set[str]:
        turler: set[str] = set()
        for yol in self.KAYNAK.rglob("*.py"):
            if "__pycache__" in yol.parts:
                continue
            metin = yol.read_text(encoding="utf-8")
            turler |= set(re.findall(r'kind=["\']([a-z_]+)["\']', metin))
            # `save_artifact` ve `record_tasks` turu semadaki enum ile sinirlar.
            for blok in re.findall(r'"kind":\s*\{[^}]*?"enum":\s*\[([^\]]*)\]', metin):
                turler |= set(re.findall(r'"([a-z_]+)"', blok))
        # `classify()` belge turleri
        turler |= {"code", "data", "web", "doc"}
        return {t for t in turler if t}

    def test_the_scan_finds_the_known_ones(self):
        turler = self._kodun_urettigi_turler()
        assert {"screenshot", "package", "mockup", "web"} <= turler, turler

    @pytest.mark.parametrize("dil", LANGS)
    def test_every_kind_is_translated(self, i18n, dil):
        eksik = sorted(
            k for k in self._kodun_urettigi_turler() if f"kind.{k}" not in i18n[dil]
        )
        assert not eksik, f"{dil}: sozlukte karsiligi olmayan tur: {eksik}"


class TestTheProviderListSpeaksTheUsersLanguage:
    """Saglayici listesi sunucudan HAZIR METIN olarak geliyordu.

    "vLLM (yerel)", "Diger (elle adres)", "Docker ile calistiriyorsaniz
    HOST portunu yazin." -- hepsi Ingilizce arayuzde de Turkceydi, cunku
    dil degistirmek sunucuda yazilmis bir metne dokunmaz. i18n.js'in bas
    yorumu tam bunu yasakliyor.
    """

    def _presets(self):
        from deerx.llm.providers import PRESETS

        return PRESETS

    def test_labels_are_bare_product_names(self):
        """Parantez ici bir ACIKLAMADIR; aciklamalar sozluge aittir.

        "vLLM (yerel)" tam boyle kacmisti. `local` bayragi zaten var ve
        sifati arayuz kendi dilinde ekliyor; etikete yazilinca Ingilizce
        ekranda da Turkce goruntuluyordu. Karakter taramasi yetmez --
        "yerel" kelimesinde Turkce'ye ozgu tek harf yok.
        """
        for preset in self._presets():
            assert "(" not in preset.label, f"{preset.key}: {preset.label}"
            assert not re.search(r"[ığşİĞŞıçöü]", preset.label), preset.key

    def test_notes_are_keys_not_sentences(self):
        kaynak = (STATIC_DIR.parent.parent / "llm" / "providers.py").read_text(
            encoding="utf-8"
        )
        assert "note=" not in kaynak, "serbest metin not geri gelmis"
        for preset in self._presets():
            # Anahtar; cumle degil. Cumlede bosluk olur, anahtarda olmaz.
            assert " " not in preset.note_key, preset.key

    @pytest.mark.parametrize("dil", LANGS)
    def test_every_note_key_resolves(self, i18n, dil):
        eksik = [
            p.key for p in self._presets()
            if p.note_key and p.note_key not in i18n[dil]
        ]
        assert not eksik, f"{dil}: notu cevrilemeyen saglayici: {eksik}"

    @pytest.mark.parametrize("dil", LANGS)
    def test_every_label_key_resolves(self, i18n, dil):
        eksik = [
            p.key for p in self._presets()
            if p.label_key and p.label_key not in i18n[dil]
        ]
        assert not eksik, f"{dil}: etiketi cevrilemeyen saglayici: {eksik}"

    def test_the_local_tag_is_added_by_the_interface(self, i18n):
        """Etiket marka adidir; "yerel" sifatini arayuz ekler."""
        assert "settings.presetLocalLabel" in i18n["tr"]
        assert "{name}" in i18n["tr"]["settings.presetLocalLabel"]
        assert any(p.local for p in self._presets())

    @pytest.mark.parametrize("dil", LANGS)
    def test_every_run_title_key_resolves(self, i18n, dil):
        """Kosu basligi anahtarlari sunucuda uretilir, arayuzde cizilir."""
        for anahtar in ("runs.titlePhase", "runs.titlePhases", "runs.titleTask",
                        "runs.titleTaskOnly", "runs.titlePlan", "runs.titlePlanOnly"):
            assert anahtar in i18n[dil], f"{dil}: {anahtar}"
