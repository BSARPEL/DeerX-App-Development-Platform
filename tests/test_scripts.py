"""Baslatma betikleri: sozdizimi, kodlama ve iki platform arasinda tutarlilik.

Betikler CI'da calistirilamaz (sunucu ayaga kaldirmak gerekir), ama sessizce
kirilan seyleri statik olarak yakalayabiliriz. Buradaki her test gercekten
yasanmis bir hatanin karsiligidir.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from deerx.config import DEFAULT_PORT

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SH = SCRIPTS / "deerx.sh"
PS1 = SCRIPTS / "deerx.ps1"
CMD = SCRIPTS / "deerx.cmd"

COMMANDS = {"setup", "passwd", "start", "stop", "restart", "status", "logs", "help"}


class TestPresence:
    def test_all_three_launchers_exist(self):
        for path in (SH, PS1, CMD):
            assert path.is_file(), path

    def test_shell_script_has_a_shebang(self):
        assert SH.read_text(encoding="utf-8").startswith("#!/usr/bin/env sh")

    def test_shell_script_uses_lf_line_endings(self):
        """CRLF'li bir .sh dosyasi Linux'ta `bad interpreter: sh^M` verir.

        Windows'ta `Path.write_text` satir sonu cevirdigi icin bu dosya
        farkinda olmadan CRLF'e donebiliyor; .gitattributes deponun icini
        korur, bu test calisma kopyasini.
        """
        assert b"\r\n" not in SH.read_bytes(), (
            "deerx.sh CRLF ile kaydedilmis; Linux/macOS'ta calismaz."
        )


class TestGitAttributes:
    """Satir sonu kurallari depoda sabitlenmeli."""

    def test_gitattributes_pins_shell_scripts_to_lf(self):
        attrs = (SCRIPTS.parent / ".gitattributes")
        assert attrs.is_file(), ".gitattributes yok"
        body = attrs.read_text(encoding="utf-8")
        assert re.search(r"^\*\.sh\s+text\s+eol=lf", body, re.M), body


class TestPowerShellEncoding:
    """PS1 dosyasi UTF-8 BOM ile kaydedilmeli.

    BOM olmadan Windows PowerShell 5.1 dosyayi ANSI (cp1254) sanir; UTF-8
    uzun tire `—` (E2 80 94) cp1254'te `â€"` olarak cozulur. Icindeki cift
    tirnak bir dizgi acar ve dosyanin geri kalanini yutar — betik hic
    ayristirilamaz.
    """

    def test_starts_with_utf8_bom(self):
        assert PS1.read_bytes().startswith(b"\xef\xbb\xbf"), (
            "deerx.ps1 UTF-8 BOM ile baslamiyor; Windows PowerShell 5.1 onu "
            "ANSI sanar ve Turkce karakterler betigi bozar."
        )

    def test_decodes_as_utf8(self):
        PS1.read_bytes()[3:].decode("utf-8")


class TestFragilePatterns:
    def test_no_backtick_line_continuation(self):
        """Devamin ardina dusen tek bir bosluk tum blogu bozar; splat kullanin."""
        body = PS1.read_bytes()[3:].decode("utf-8")
        offenders = [
            f"L{i}: {line.strip()}"
            for i, line in enumerate(body.splitlines(), start=1)
            if line.rstrip("\r\n").endswith("`")
        ]
        assert not offenders, "ters tirnakli satir devami: " + "; ".join(offenders)

    def test_native_exe_stderr_is_not_piped(self):
        """PowerShell 5.1'de bir exe'nin stderr'i `$ErrorActionPreference='Stop'`
        altinda betigi sonlandirir. `taskkill ... 2>&1` durdurmayi yarim
        birakiyordu; .NET cagrilari (Stop-Process) bu tuzagi tasimaz.
        """
        body = PS1.read_bytes()[3:].decode("utf-8")
        # Yorumlar haric: aciklama metninde adi gecmesi sorun degil.
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert "taskkill" not in code, (
            "native taskkill kullanilmis; stderr'i betigi sonlandirabilir"
        )
        assert "Stop-Process" in code


class TestLauncherResolution:
    """Betik, kurulu `deerx` komutunu bulabilmeli."""

    def test_the_shell_script_finds_the_windows_launcher_too(self):
        """Sanal ortamin ikili dizini POSIX'te `bin/`, Windows'ta `Scripts/`.

        OLCULDU (2026-09-02): `deerx.sh` yalnizca `bin/`e bakiyordu ve Git
        Bash altinda kurulumu YERINDE OLDUGU HALDE bulamiyordu. "Kurulu
        degil" yoluna dusup `uv run` cagiriyor, o da ortami tazelemeye
        kalkiyordu; sunucu calisirken `deerx.exe` kilitli oldugu icin her
        baslatma "os error 32" ile oluyordu.

        Yorum satirlari cikariliyor: bu hatanin aciklamasi da dosyada
        duruyor ve testin kendi aciklamasini bulup gecmesi olurdu.
        """
        satirlar = [
            satir for satir in SH.read_text(encoding="utf-8").splitlines()
            if not satir.lstrip().startswith("#")
        ]
        for yol in (".venv/bin/deerx", ".venv/Scripts/deerx.exe"):
            assert any(yol in satir for satir in satirlar), (
                f"deerx.sh {yol} yolunu aramiyor"
            )



class TestConsoleOutputIsAscii:
    """Windows konsolu (cp857/cp1254) uzun tire ve uc noktayi dusurur.

    Kullaniciya gorunen metinler ASCII olmali; aksi halde cikti bozuk gorunur.
    """

    @staticmethod
    def _visible_strings(text: str) -> list[str]:
        found = []
        for pattern in (r"Write-Host\s+(['\"])(.*?)\1", r"say\s+(['\"])(.*?)\1"):
            found += [m.group(2) for m in re.finditer(pattern, text)]
        return found

    @pytest.mark.parametrize("script", [SH, PS1], ids=["sh", "ps1"])
    def test_user_facing_text_is_ascii(self, script: Path):
        raw = script.read_bytes()
        text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
        bad = [s for s in self._visible_strings(text) if not s.isascii()]
        assert not bad, f"ASCII disi konsol metni: {bad}"

    def test_cmd_files_are_ascii_throughout(self):
        """Toplu is dosyalarinin TAMAMI ASCII olmali, yorumlari dahil.

        `.cmd` dosyasi kod sayfasina gore cozulur (Turkce Windows'ta
        cp857). Icindeki UTF-8 bir uzun tire orada baska bayta donusur;
        yorumda zararsiz gorunse de bir `echo` satirina tasindiginda
        cikti bozulur, bir dizgide ise ayristirmayi kirar.

        `deerx.cmd`nin ikinci satirinda boyle bir tire vardi ve
        `.sh`/`.ps1` icin yazilmis denetim `.cmd`ye hic bakmadigi icin
        gorulmemisti.
        """
        kotu = {}
        for yol in sorted(SCRIPTS.glob("*.cmd")):
            metin = yol.read_bytes().decode("utf-8", "replace")
            satirlar = [
                f"{n}: {s.strip()[:60]}"
                for n, s in enumerate(metin.splitlines(), start=1)
                if not s.isascii()
            ]
            if satirlar:
                kotu[yol.name] = satirlar
        assert not kotu, f"ASCII disi .cmd satiri: {kotu}"


class TestCommandParity:
    """Iki betik ayni komutlari sunmali; biri digerinden geride kalmasin."""

    def test_shell_script_handles_every_command(self):
        body = SH.read_text(encoding="utf-8")
        case_block = body.split("case \"$COMMAND\" in", 1)[1]
        for command in COMMANDS:
            assert re.search(rf"^\s*{command}[)|]", case_block, re.M), command

    def test_powershell_validates_the_same_set(self):
        body = PS1.read_bytes()[3:].decode("utf-8")
        match = re.search(r"\[ValidateSet\(([^)]*)\)\]", body)
        assert match, "ValidateSet yok"
        declared = {v.strip().strip("'\"") for v in match.group(1).split(",")}
        assert declared == COMMANDS

    def test_both_use_the_same_default_port(self):
        assert f"PORT={DEFAULT_PORT}" in SH.read_text(encoding="utf-8")
        assert f"$Port = {DEFAULT_PORT}" in PS1.read_bytes()[3:].decode("utf-8")

    def test_both_keep_state_in_the_workspace_data_dir(self):
        """PID ve gunluk `.deerx/` altinda; her calisma alani bagimsiz olmali."""
        sh = SH.read_text(encoding="utf-8")
        ps = PS1.read_bytes()[3:].decode("utf-8")
        assert 'DATA_DIR="$WORKSPACE/.deerx"' in sh
        assert "Join-Path $Workspace '.deerx'" in ps
        for body in (sh, ps):
            assert "server.pid" in body and "server.log" in body


class TestShellSyntax:
    def test_shell_script_parses(self):
        sh = shutil.which("sh") or shutil.which("bash")
        if not sh:
            pytest.skip("sh/bash yok")
        result = subprocess.run(
            [sh, "-n", str(SH)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    def test_powershell_script_parses(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            pytest.skip("PowerShell yok")
        probe = (
            "$e=$null; "
            f"$null=[System.Management.Automation.Language.Parser]::ParseFile('{PS1}',"
            "[ref]$null,[ref]$e); "
            "if($e.Count -gt 0){$e[0].Message; exit 1}"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", probe],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestHealthProbe:
    """Yoklanan ucun herkese acik kalmasi.

    Betikler acilisi `/api/overview` ile yokluyordu. Kimlik dogrulama
    eklenince o uc 401 dondurmeye basladi; `curl -f` ve `Invoke-WebRequest`
    401'i hata sayar. Sonuc: sunucu saglam ayaga kalkiyor, `start` 90 saniye
    bekleyip "yanit vermedi" diyerek 1 ile cikiyor, `status` ise calisan
    sunucuya "YANIT YOK" diyordu. Bag statik degil anlamsal oldugu icin
    hicbir sozdizimi kontrolu yakalayamazdi.
    """

    @staticmethod
    def _code(script: Path) -> str:
        raw = script.read_bytes()
        text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
        # Yorumlar disarida: aciklamada `/api/overview` gecmesi sorun degil,
        # yoklanan adres olmasi sorun.
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    @pytest.mark.parametrize("script", [SH, PS1], ids=["sh", "ps1"])
    def test_probed_paths_are_public(self, script: Path):
        from deerx.web.app import PUBLIC_PATHS

        probed = set(re.findall(r"/api/[a-z0-9/_-]+", self._code(script)))
        assert probed, "betik hicbir uc yoklamiyor"
        private = probed - set(PUBLIC_PATHS)
        assert not private, (
            f"{script.name} kimlik isteyen uc yokluyor: {sorted(private)}. "
            "Korumali bir uc 401 doner ve saglam sunucu 'yanit vermiyor' gorunur."
        )

    def test_both_scripts_probe_the_same_endpoint(self):
        assert (
            set(re.findall(r"/api/[a-z0-9/_-]+", self._code(SH)))
            == set(re.findall(r"/api/[a-z0-9/_-]+", self._code(PS1)))
        )


class TestProcessIdentification:
    """Bir sureci "bizim" saymadan once komut satirina bakilmali.

    Betik yalnizca "deerx" gecmesine bakiyordu; betigin kendi komut satirinda
    da geciyor, yani kendini sunucu sanip oldurebilirdi.
    """

    @staticmethod
    def _text(script: Path) -> str:
        raw = script.read_bytes()
        return raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")

    def test_shell_excludes_the_scripts_themselves(self):
        body = self._text(SH)
        assert "*deerx.sh*" in body and "*deerx.ps1*" in body

    def test_powershell_checks_the_process_name(self):
        body = self._text(PS1)
        assert "OurNames" in body, "surec adi dogrulanmiyor"
        assert "'*deerx*serve*'" in body, "komut satirinda 'serve' aranmiyor"


class TestShellDecisions:
    """Karar mantigi: sahte `lsof`/`ps`/`curl`/`nohup` ile, sunucu baslatmadan.

    Gelistirici Windows'ta calisiyor; bu betigi hedef isletim sisteminde
    kosturmak her zaman mumkun degil. Araclari sahteleyerek en azindan
    kararlari sinariz: kim bizim surecimiz, port kimde, ne raporlaniyor.

    `nohup` da sahtelenir. Bir senaryo yanlislikla baslatma yoluna girerse
    gercek bir sunucu ayaga kalkip testten sag cikmasin; nohup'siz surum bir
    kez 60 saniye zaman asimina dusup arkada yetim sunucu birakmisti.
    """

    OURS = "python /opt/deerx/.venv/bin/deerx serve --host 127.0.0.1 --port 8791"

    @pytest.fixture()
    def run(self, tmp_path: Path):
        shell = shutil.which("sh") or shutil.which("bash")
        if not shell:
            pytest.skip("sh/bash yok")

        fake, workspace = tmp_path / "bin", tmp_path / "ws"
        (workspace / ".deerx").mkdir(parents=True)
        fake.mkdir()
        stubs = {
            # -Fn -> dinlenen adres, digeri -> portu tutan PID
            "lsof": 'for a in "$@"; do case "$a" in -Fn) cat "$D/addr" 2>/dev/null; '
                    'exit 0 ;; esac; done; cat "$D/owner" 2>/dev/null\n',
            "ps": 'cat "$D/args" 2>/dev/null\n',
            "curl": '[ -f "$D/up" ] && exit 0\nexit 7\n',
            "nohup": 'printf "STUB-LAUNCH %s\\n" "$*" >>"$D/launched"\n',
        }
        for name, body in stubs.items():
            path = fake / name
            path.write_text("#!/usr/bin/env sh\n" + body, encoding="utf-8", newline="\n")
            path.chmod(0o755)

        data = tmp_path / "fake"
        data.mkdir()

        # Kabugun sinyal gonderebilecegi bir PID lazim: betik `kill -0` ile
        # surecin yasadigini dogruluyor. Windows'ta Git Bash kendi PID
        # tablosunu kullanir, `os.getpid()` (Windows PID'i) oradan gorunmez;
        # o PID ile her sey "olmus surec" gorunur ve testler yanlis dallari
        # sinar. Kabugun kendi bildirdigi PID her iki dunyada da gecerli.
        sleeper = subprocess.Popen(
            [shell, "-c", "echo $$; exec sleep 300"],
            stdout=subprocess.PIPE, text=True,
        )
        alive = int(sleeper.stdout.readline().strip())

        def _run(command: str, *, owner=None, args=None, addr=None, pid=None):
            for name in ("owner", "args", "addr", "up", "launched"):
                (data / name).unlink(missing_ok=True)
            if owner is not None:
                (data / "owner").write_text(f"{alive}\n", encoding="utf-8")
            if args is not None:
                (data / "args").write_text(f"{args}\n", encoding="utf-8")
            if addr is not None:
                (data / "addr").write_text(f"n127.0.0.1:{addr}\n", encoding="utf-8")
            pid_file = workspace / ".deerx" / "server.pid"
            if pid is None:
                pid_file.unlink(missing_ok=True)
            else:
                pid_file.write_text(str(alive), encoding="utf-8")
            env = {
                **os.environ,
                "D": str(data),
                "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
            }
            result = subprocess.run(
                [shell, str(SH), command, "-w", str(workspace)],
                capture_output=True, text=True, timeout=60, env=env,
            )
            return result.stdout + result.stderr

        try:
            yield _run
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=10)

    def test_nothing_running_reads_as_stopped(self, run):
        assert "Durum   : durmus" in run("status")

    def test_untracked_server_is_reported_not_denied(self, run):
        """Calisan bir sunucuya "durmus" demek, ikinci bir tane baslatmaya
        ve "port dolu" hatasina goturur."""
        out = run("status", owner=True, args=self.OURS)
        assert "DIKKAT" in out and "betik disinda baslatilmis" in out

    def test_foreign_process_is_not_mistaken_for_deerx(self, run):
        out = run("status", owner=True, args="nginx: master process")
        assert "baska bir program kullaniyor" in out

    def test_foreign_process_is_never_killed(self, run):
        out = run("stop", owner=True, args="nginx: master process")
        assert "Dokunulmadi" in out

    def test_the_script_does_not_take_itself_for_the_server(self, run):
        out = run("status", pid=True, args="sh ./scripts/deerx.sh status")
        assert "Durum   : durmus" in out

    def test_real_port_is_reported_when_it_differs(self, run):
        """PID dosyasi calisma alanina ait, port ise bir parametre."""
        out = run("status", pid=True, args=self.OURS, addr=9000)
        assert "dinlenen port 9000" in out

    def test_start_refuses_when_the_workspace_runs_elsewhere(self, run):
        out = run("start", pid=True, args=self.OURS, addr=9000)
        assert "zaten 9000 portunda" in out

    def test_start_adopts_our_own_untracked_server(self, run):
        out = run("start", owner=True, args=self.OURS)
        assert "tazelendi" in out

    def test_start_refuses_a_port_held_by_something_else(self, run):
        """Proje hep ayni portta calisir; mesaj baska porta kacmayi degil
        portu bosaltmayi anlatmali ve tutan programi adiyla soylemeli."""
        out = run("start", owner=True, args="nginx: master process")
        assert "nginx" in out
        assert "portunda calisir" in out


class TestLocalCheck:
    """Deponun TEK dogrulamasi: push'tan once kosan yerel denetim.

    GitHub Actions is akisi 2026-09-02'de kullanicinin istegiyle kaldirildi.
    Onceden bu sinif yerel betikleri `ci.yml` ile karsilastiriyordu; artik
    karsilastirilacak bir sey yok ve denetimin kendisi son savunma.
    """

    SH = ROOT / "scripts" / "check.sh"
    PS1 = ROOT / "scripts" / "check.ps1"
    HOOK = ROOT / ".githooks" / "pre-push"

    def test_the_scripts_and_the_hook_exist(self):
        for yol in (self.SH, self.PS1, self.HOOK):
            assert yol.is_file(), yol

    def test_shell_files_use_lf_and_a_shebang(self):
        """CRLF'li bir kanca Linux'ta `bad interpreter` verir; git onu
        sessizce atlamaz, push'u kirar."""
        for yol in (self.SH, self.HOOK):
            ham = yol.read_bytes()
            assert b"\r\n" not in ham, f"{yol.name} CRLF ile kaydedilmis"
            assert ham.startswith(b"#!/usr/bin/env sh"), yol.name

    def test_powershell_starts_with_a_utf8_bom(self):
        assert self.PS1.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_the_check_runs_both_lint_and_tests(self):
        """Ikisi de kosmali; biri dusunce denetim yarim kalir.

        Eskiden bu, `ci.yml` ile karsilastirilarak dogrulaniyordu. CI
        kaldirilinca liste burada ACIKCA yaziliyor -- karsilastirilacak
        bir dosya kalmadi, ve bir komutun sessizce dusmesi artik hicbir
        yerde yakalanmaz.
        """
        for komut in ("ruff check src tests", "pytest -q"):
            for yol in (self.SH, self.PS1):
                metin = yol.read_text(encoding="utf-8-sig")
                assert komut in metin, f"{yol.name} `{komut}` kosmuyor"

    def test_no_workflow_claims_to_verify_anything(self):
        """Kullanici (2026-09-02) GitHub'a hicbir sey yansimasin istedi.

        Bir is akisi dosyasi geri eklenirse push tekrar GitHub'da kosar ve
        basarisiz kosular e-posta olarak doner. Bu test o donusu bir
        surprize degil, bilincli bir karara baglar.
        """
        akislar = ROOT / ".github" / "workflows"
        varsa = sorted(p.name for p in akislar.glob("*.y*ml")) if akislar.is_dir() else []
        assert not varsa, (
            f"is akisi geri gelmis: {varsa}. Istenirse eklenebilir, ama "
            "kullanici GitHub'da kosu istemiyordu -- once ona sorun."
        )

    def test_the_hook_does_not_forward_gits_arguments(self):
        """Git kancayi `<uzak> <url>` ile cagirir; `check.sh` bunlari
        secenek sanir.

        OLCULDU: `check.sh` bilinmeyen secenekleri reddetmeye baslayinca
        kanca "Bilinmeyen secenek: origin" ile HER push'u dusurdu.
        Onceden calisiyordu cunku betik bilinmeyen argumanlari sessizce
        yutuyordu -- yani yazim hatasi da yutuluyordu. Katilik dogru
        karar; hatali olan, git'in argumanlarini betige gecirmekti.
        """
        metin = self.HOOK.read_text(encoding="utf-8")
        cagri = [s for s in metin.splitlines() if "check.sh" in s and not s.lstrip().startswith("#")]
        assert len(cagri) == 1, cagri
        assert '"$@"' not in cagri[0], cagri[0]

    def test_the_check_refuses_an_unknown_option(self, sahte_uv):
        """Sessizce yutulan bir secenek, kosuldugu sanilan bir secenektir.

        `--fasst` yazan biri hizli kostugunu sanir, tum suiti kosturur.
        Bu katilik bir kez pahaliya patladi -- `pre-push`, git'in verdigi
        `origin` argumanini denetime geciriyordu ve HER push dustu -- ama
        dogru cozum katiligi geri almak degil, kancayi duzeltmekti.

        Metnin dosyada durmasi degil, betigin GERCEKTEN reddetmesi
        sinanir: cikis 2 ve hicbir `uv` cagrisi yok.
        """
        sonuc, cagrilar = sahte_uv("origin")
        assert sonuc.returncode == 2, sonuc.stdout + sonuc.stderr
        assert not cagrilar, cagrilar

    def test_the_hook_can_be_skipped(self):
        """Kacis yolu olmayan bir kanca, insanlarin kancayi tumden
        kaldirmasiyla sonuclanir."""
        assert "--no-verify" in self.HOOK.read_text(encoding="utf-8")

    def test_uv_run_does_not_resync_the_environment(self):
        """`uv run` ortami tazeler ve paketi yeniden kurar; calisan bir DeerX
        sunucusu `deerx.exe` dosyasini kilitledigi icin kurulum "os error 32"
        ile duser ve denetim HIC baslamaz. Olculdu."""
        for yol in (self.SH, self.PS1):
            metin = yol.read_text(encoding="utf-8-sig")
            for satir in metin.splitlines():
                s = satir.strip()
                if s.startswith("uv run "):
                    assert "--no-sync" in s, f"{yol.name}: {s}"

    @pytest.fixture()
    def sahte_uv(self, tmp_path):
        """`uv`yi sahteleyip check.sh'i GERCEKTEN kosturur.

        Statik bir dizgi denetimi yeterli degildi: `--pythons) COKLU=1`
        satirini `--pythons) : ;;` yapmak testi kirmiyordu, cunku aranan
        metin dosyada duruyordu. Sahte `uv` her cagriyi ortam degiskeniyle
        birlikte kaydeder; boylece SOYLENEN degil YAPILAN sinanir.
        """
        shell = shutil.which("sh") or shutil.which("bash")
        if not shell:
            pytest.skip("sh/bash yok")

        sahte = tmp_path / "bin"
        sahte.mkdir()
        kayit = tmp_path / "cagrilar.txt"
        (sahte / "uv").write_text(
            "#!/usr/bin/env sh\n"
            'printf "%s|%s\\n" "${UV_PROJECT_ENVIRONMENT:-ANA}" "$*"'
            f' >> "{kayit.as_posix()}"\n',
            encoding="utf-8", newline="\n",
        )
        (sahte / "uv").chmod(0o755)

        def _kos(*args):
            kayit.write_text("", encoding="utf-8")
            ortam = {**os.environ, "PATH": f"{sahte}{os.pathsep}{os.environ['PATH']}"}
            # Cevredeki UV_PROJECT_ENVIRONMENT COCUGA GECMEMELI. Bu testler
            # `check.sh --pythons` tarafindan yan bir ortam icinde de kosar;
            # orada degisken `.venv-check-3.11` olur ve sahte `uv` "ANA"
            # yerine onu kaydeder. Test o zaman betigin ne YAPTIGINI degil,
            # kabugun ne TASIDIGINI olcmus olur -- ve 3.11 kosusunda
            # duserdi (olculdu). Ayni sinif hata bu depoda daha once
            # DEERX_WORKSPACE ile de yasandi.
            ortam.pop("UV_PROJECT_ENVIRONMENT", None)
            sonuc = subprocess.run(
                [shell, str(self.SH), *args],
                capture_output=True, text=True, timeout=120,
                cwd=str(ROOT),
                env=ortam,
            )
            satirlar = [s for s in kayit.read_text(encoding="utf-8").splitlines() if s]
            return sonuc, satirlar

        return _kos

    def test_without_the_flag_only_the_current_environment_runs(self, sahte_uv):
        _sonuc, cagrilar = sahte_uv()
        assert all(c.startswith("ANA|") for c in cagrilar), cagrilar
        assert any("ruff check src tests" in c for c in cagrilar)
        assert any("pytest -q" in c for c in cagrilar)

    def test_the_stub_is_isolated_from_the_developers_shell(self, sahte_uv, monkeypatch):
        """Cevredeki `UV_PROJECT_ENVIRONMENT` bu testlere karismamali.

        OLCULDU (2026-09-02): `check.sh --pythons` bu suiti yan bir ortam
        icinde de kosturur. Orada degisken `.venv-check-3.11`dir ve
        fixture onu cocuga geciriyordu; sahte `uv` "ANA" yerine onu
        kaydetti ve ustteki test 3.11 kosusunda dustu -- betikte hicbir
        sey bozuk degilken. Yalitim olmadan bu testler betigin ne
        YAPTIGINI degil, kabugun ne TASIDIGINI olcer.

        Bu test o yalitimi ayakta tutar: yalitim kaldirilirsa BURADA
        duser, aylar sonra baska bir Python surumunde degil.
        """
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", ".venv-check-3.11")
        _sonuc, cagrilar = sahte_uv()
        assert cagrilar
        assert all(c.startswith("ANA|") for c in cagrilar), cagrilar

    def test_the_flag_runs_the_suite_under_each_python(self, sahte_uv):
        """Kullanici (2026-09-02) "bilgisayarimda olabilir ama github'da
        olmasin" dedi. Isletim sistemi farkini tek makine veremez, surum
        farkini verebilir."""
        _sonuc, cagrilar = sahte_uv("--pythons")
        for surum in ("3.11", "3.13"):
            ortam = f".venv-check-{surum}"
            assert any(c.startswith(f"{ortam}|") and "sync" in c and surum in c
                       for c in cagrilar), (surum, cagrilar)
            assert any(c.startswith(f"{ortam}|") and "pytest" in c
                       for c in cagrilar), (surum, cagrilar)

    def test_the_side_run_never_touches_the_main_environment(self, sahte_uv):
        """Ana `.venv` bozulursa gelistirici calisan kurulumunu kaybeder:
        yan surumler AYRI bir ortamda kurulmali."""
        _sonuc, cagrilar = sahte_uv("--pythons")
        yan = [c for c in cagrilar if "3.11" in c or "3.13" in c]
        assert yan
        assert not any(c.startswith("ANA|") for c in yan), yan

    def test_powershell_restores_the_environment_variable(self):
        """Degisken geri alinmazsa o kabuktaki SONRAKI her `uv` cagrisi
        yan ortama gider -- sessiz ve kafa karistirici."""
        ps = self.PS1.read_text(encoding="utf-8-sig")
        blok = ps.split("if ($Pythons)", 1)[1]
        assert "finally" in blok
        assert "Remove-Item Env:UV_PROJECT_ENVIRONMENT" in blok

    def test_the_side_environments_are_not_committed(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        assert ".venv-*/" in ignore


class TestPytestInvocation:
    """Suit her cagirma biciminde toplanabilmeli."""

    def test_the_repo_root_is_on_the_python_path(self):
        """`tests/test_theme.py` `tests.test_web`den ice aktariyor.

        `python -m pytest` calisma dizinini sys.path'e kendisi ekler, ama
        `pytest` konsol betigi EKLEMEZ. CI tam olarak `uv run pytest -q`
        calistiriyor, yani suit CI'da toplama sirasinda cokuyordu:

            tests/test_theme.py:19: from tests.test_web import TestPalette
            E   ModuleNotFoundError: No module named 'tests'

        Yerelde `python -m pytest` kullandigimiz icin bu hic gorunmedi.
        """
        metin = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        satir = next(
            (s for s in metin.splitlines() if s.strip().startswith("pythonpath")), None
        )
        assert satir, "pythonpath ayari yok"
        assert '"."' in satir, (
            "kok dizin pythonpath'te olmali; yoksa `pytest` (konsol betigi) "
            f"ile toplama cokuyor: {satir.strip()}"
        )


class TestLocalDefaults:
    """`scripts/deerx.local.conf`: bu makineye ozel varsayilanlar.

    Kullanici sunucusunu her seferinde `-H 0.0.0.0 -w .../demo` yazarak
    baslatmak istemiyor. Betigin varsayilanini degistirmek ise cozum
    degil: depo herkese acik ve varsayilani `0.0.0.0` yapmak klonlayan
    herkesin DeerX'ini aga acardi. Ayar bu yuzden surum kontrolune
    girmeyen bir dosyada.
    """

    CONF = SCRIPTS / "deerx.local.conf"
    ORNEK = SCRIPTS / "deerx.local.conf.example"

    def test_the_example_is_shipped(self):
        """Ornek olmadan ozellik yok sayilir: kimse olmayan bir dosyanin
        adini tahmin etmez."""
        assert self.ORNEK.is_file()
        metin = self.ORNEK.read_text(encoding="utf-8")
        for anahtar in ("PORT", "HOST", "WORKSPACE"):
            assert anahtar in metin, anahtar

    def test_the_real_file_is_never_committed(self):
        """Icinde bu makineye ozel bir yol ve `0.0.0.0` var; depoya
        girerse hem anlamsiz hem tehlikeli olur."""
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        assert "scripts/deerx.local.conf" in ignore

    def test_the_shipped_defaults_stay_loopback(self):
        """Depodaki varsayilan ASLA disari acik olmamali."""
        assert "HOST=127.0.0.1" in self.ORNEK.read_text(encoding="utf-8")
        assert re.search(r"^HOST=127\.0\.0\.1$", SH.read_text(encoding="utf-8"), re.M)
        assert "$BindHost = '127.0.0.1'" in PS1.read_text(encoding="utf-8-sig")

    def test_both_scripts_read_the_same_file(self):
        """Iki platform ayni dosyayi okumali; biri okuyup digeri okumazsa
        Windows'ta calisan kurulum Linux'ta baska adrese baglanir."""
        assert "deerx.local.conf" in SH.read_text(encoding="utf-8")
        assert "deerx.local.conf" in PS1.read_text(encoding="utf-8-sig")

    def test_neither_script_sources_the_file(self):
        """Dosya kabuk komutu olarak CALISTIRILMAMALI.

        `.` ile yuklemek ya da `Invoke-Expression`, ayar dosyasina yazilan
        her seyi calistirmak demekti -- bir ayar dosyasindan beklenmeyen
        bir yetki.
        """
        sh = SH.read_text(encoding="utf-8")
        assert ". $LOCAL_CONF" not in sh and 'source "$LOCAL_CONF"' not in sh
        ps = PS1.read_text(encoding="utf-8-sig")
        assert "Invoke-Expression" not in ps
        assert ". $LocalConf" not in ps

    def test_powershell_announces_what_it_read(self):
        """Sessiz kalsaydi biri `deerx.ps1 start` yazip sunucunun neden
        0.0.0.0'a baglandigini anlamazdi.

        Yalnizca PowerShell tarafi statik olarak sinaniyor: bu betik
        gelistirme makinesinde kosturulamiyor. Kabuk tarafinin AYNI
        davranisi asagida gercekten kosturularak dogrulaniyor
        (`test_the_file_supplies_the_defaults` satiri bekliyor,
        `test_without_the_file_nothing_changes` yoklugunu bekliyor) --
        metnin dosyada GECMESINE bakan bir test, satir bir kosula
        baglanip hic yazilmaz hale geldiginde de yesil kalirdi.
        """
        ps = PS1.read_text(encoding="utf-8-sig")
        assert "Yerel ayar:" in ps
        assert "if ($LocalUsed.Count) {" in ps, (
            "bildirim bir kosula baglanmali; kosulsuz her komutta yazardi"
        )

    def test_powershell_lets_the_command_line_win(self):
        """Acikca verilen bir parametre dosyadan gelen degeri ezmeli.

        PowerShell'de bir parametrenin varsayilani ile acikca verilmis
        degeri ayirt etmenin tek yolu `$PSBoundParameters`.
        """
        ps = PS1.read_text(encoding="utf-8-sig")
        for ad in ("'Port'", "'BindHost'", "'Workspace'"):
            assert f"$PSBoundParameters.ContainsKey({ad})" in ps, ad

    # ------------------------------------------------------------------ #
    # Davranis: betigi gercekten kosturuyoruz.
    # ------------------------------------------------------------------ #
    @pytest.fixture()
    def kos(self, tmp_path: Path):
        """Betigin bir KOPYASINI kendi `scripts/` dizininde kosturur.

        Dosya `$SCRIPT_DIR`den okundugu icin betik ile ayar dosyasi ayni
        dizinde olmali; deponun kendi `deerx.local.conf`u testi
        etkilemesin diye kopya ayri bir yerde duruyor.
        """
        shell = shutil.which("sh") or shutil.which("bash")
        if not shell:
            pytest.skip("sh/bash yok")

        betikler = tmp_path / "scripts"
        betikler.mkdir()
        kopya = betikler / "deerx.sh"
        kopya.write_bytes(SH.read_bytes())
        calisma = tmp_path / "ws"
        (calisma / ".deerx").mkdir(parents=True)

        def _kos(conf: str | None, *args: str) -> str:
            hedef = betikler / "deerx.local.conf"
            if conf is None:
                hedef.unlink(missing_ok=True)
            else:
                hedef.write_text(conf, encoding="utf-8", newline="\n")
            sonuc = subprocess.run(
                [shell, str(kopya), "status", *args],
                capture_output=True, text=True, timeout=60,
                cwd=str(calisma),
            )
            return sonuc.stdout + sonuc.stderr

        return _kos

    def test_without_the_file_nothing_changes(self, kos):
        cikti = kos(None)
        assert "Yerel ayar" not in cikti
        assert f"127.0.0.1:{DEFAULT_PORT}" in cikti

    def test_the_file_supplies_the_defaults(self, kos):
        cikti = kos("PORT=9123\nHOST=0.0.0.0\n")
        assert "Yerel ayar" in cikti
        assert "0.0.0.0:9123" in cikti

    def test_the_command_line_beats_the_file(self, kos):
        """Onceklik: komut satiri > dosya. Tersi olsaydi tek seferlik bir
        port denemek imkansiz olurdu."""
        cikti = kos("PORT=9123\nHOST=0.0.0.0\n", "-p", "9999")
        assert "0.0.0.0:9999" in cikti
        assert "9123" not in cikti

    def test_comments_and_blank_lines_are_ignored(self, kos):
        cikti = kos("# yorum\n\n   \nPORT=9124\n")
        assert "127.0.0.1:9124" in cikti

    def test_quotes_and_spaces_are_trimmed(self, kos):
        cikti = kos('PORT = "9125"\n')
        assert "127.0.0.1:9125" in cikti

    def test_an_unknown_key_warns_instead_of_failing(self, kos):
        """Yazim hatasi butun betigi dusurmemeli, ama sessiz de kalmamali:
        sessizce yok sayilan bir ayar, ayarin calistigi sanilan bir ayardir."""
        cikti = kos("PORT=9126\nPROT=8000\n")
        assert "PROT" in cikti
        assert "127.0.0.1:9126" in cikti

    def test_the_file_is_data_not_code(self, kos):
        """Ayar dosyasindaki bir komut CALISMAMALI.

        Dosya `.` ile yuklenseydi buradaki yerine gecme calisir ve port
        9127 olurdu; ayar dosyasina yazilan her sey kabuk komutu olarak
        kosardi. Deger AYNEN saklanmali -- gecersiz bir port `deerx`
        tarafindan reddedilir, ki dogru yer orasi.
        """
        cikti = kos("PORT=$(echo 9127)\n")
        assert "127.0.0.1:$(echo 9127)" in cikti, cikti


    def test_the_parser_uses_posix_character_classes(self):
        r"""BSD arac zinciri `\t` kacis dizisini TANIMAZ.

        macOS'ta `tr -d ' \t'` bosluk, ters bolu ve 't' HARFINI siler;
        `sed 's/^[ \t]*//'` de bir degerin basindaki 't'yi yer. Yani
        `HOST=tcp-sunucu` gibi bir satir orada sessizce `cp-sunucu`
        olurdu -- baglanti kurulamaz ve sebep gorunmez. `[:space:]`
        POSIX sinif adi; her iki zincirde ayni sey demek.

        YORUMLAR ELENIYOR: ilk yazdigimda kendi aciklama satirim `\t`
        iceriyordu ve test kodu duzeltilse de bozulsa da yesil kaliyordu.
        """
        sh = SH.read_text(encoding="utf-8")
        ayristirici = sh.split("LOCAL_CONF=", 1)[1].split("COMMAND=", 1)[0]
        kod = "\n".join(
            s for s in ayristirici.splitlines() if not s.lstrip().startswith("#")
        )
        assert kod.count("[:space:]") >= 2, "hem tr hem sed sinif adi kullanmali"
        assert "\\t" not in kod, (
            "BSD sed/tr ters-bolu-t'yi kacis saymaz; POSIX sinif adi kullanin"
        )

    def test_a_value_starting_with_t_survives(self, kos):
        """Yukaridaki hatanin somut sonucu: `t` ile baslayan bir deger."""
        cikti = kos("PORT=9131\nHOST=t-sunucu\n")
        assert "t-sunucu:9131" in cikti, cikti

class TestDoubleClickLauncher:
    """Explorer'dan cift tiklanabilen baslatici.

    Kullanici `deerx.cmd`'ye cift tikladi ve "giremiyorum" dedi. Sebep:
    Explorer hicbir arguman gecmiyor, `deerx.ps1` varsayilan olarak
    `help` calistiriyor, pencere yardimi basip aninda kapaniyor. Disaridan
    bakan biri "tikladim, bir sey olmadi" goruyor -- ve gormesi gereken de
    tam olarak buydu, cunku sunucu gercekten baslamamisti.
    """

    START = SCRIPTS / "start.cmd"

    def test_it_exists(self):
        assert self.START.is_file()

    def test_it_starts_instead_of_printing_help(self):
        metin = self.START.read_text(encoding="utf-8")
        assert 'deerx.ps1" start' in metin, "cift tiklama `start` calistirmali"

    def test_the_window_stays_open(self):
        """Pencere kapanirsa kullanici hata mesajini goremez -- ilk
        sikayet zaten buydu."""
        assert re.search(r"^pause$", self.START.read_text(encoding="utf-8"), re.M)

    def test_it_reports_failure(self):
        """`start` basarisiz olursa pencere "hazir" gibi kapanmamali."""
        metin = self.START.read_text(encoding="utf-8")
        assert "ERRORLEVEL" in metin and "Baslatilamadi" in metin

    def test_deerx_cmd_points_at_it(self):
        """Iki dosyadan hangisinin ne icin oldugu yazili olmali; yoksa
        bir sonraki kisi yine yanlisina tiklar."""
        assert "start.cmd" in CMD.read_text(encoding="utf-8")

    def test_the_command_line_wrapper_still_defaults_to_help(self):
        """`deerx.cmd` argumansiz cagrildiginda YARDIM basmali.

        Cift tiklamayi `deerx.cmd` icinde %cmdcmdline% ile tahmin etmek
        mumkundu ama PowerShell'den yapilan cagrilar da `cmd /c` olarak
        gorunur: `deerx.cmd` yardim beklenirken aga acik bir sunucu
        baslatirdi. Ayrimi tahmin yerine AYRI BIR DOSYA kuruyor.
        """
        metin = CMD.read_text(encoding="utf-8")
        assert "cmdcmdline" not in metin
        assert "%*" in metin

    @pytest.mark.parametrize("script", [CMD, START], ids=["deerx.cmd", "start.cmd"])
    def test_cmd_files_use_crlf(self, script: Path):
        """cmd.exe LF-only bir toplu is dosyasini satir ortasinda kesebilir;
        .gitattributes bu yuzden *.cmd icin eol=crlf diyor."""
        ham = script.read_bytes()
        assert ham.count(b"\r\n") > 0
        assert ham.count(b"\n") == ham.count(b"\r\n"), f"{script.name}: karisik satir sonu"


class TestPasswordCommand:
    """Yonetici parolasini kuran/sifirlayan komut, uc isletim sisteminde.

    Kullanici "sifre degistirme calismiyor" dedi. Sebep `deerx user passwd`
    degil, onun okuma yolu: `getpass` Windows'ta konsolu DOGRUDAN okur,
    boru hattindaki veriyi hic gormez ve bir betikten beslendiginde
    ciktisiz kilitlenir. Betikler artik parolayi kendileri gizli okuyup
    `--stdin` ile aktariyor.
    """

    PASSWD_CMD = SCRIPTS / "passwd.cmd"

    def test_every_platform_has_a_way_in(self):
        """Linux/macOS: deerx.sh passwd. Windows: deerx.ps1 passwd, ve
        cift tiklanabilir passwd.cmd."""
        assert "cmd_passwd" in SH.read_text(encoding="utf-8")
        assert "function Invoke-Passwd" in PS1.read_text(encoding="utf-8-sig")
        assert self.PASSWD_CMD.is_file()

    def test_the_double_click_wrapper_calls_passwd(self):
        metin = self.PASSWD_CMD.read_text(encoding="utf-8")
        assert 'deerx.ps1" passwd' in metin
        assert re.search(r"^pause$", metin, re.M), "pencere kapanirsa hata gorulmez"

    def test_both_use_the_stdin_path(self):
        """Parola ARGUMAN olarak gecirilmemeli: arguman `ps` ciktisinda ve
        Gorev Yoneticisi'nde gorunur, kabuk gecmisine yazilir."""
        for metin in (SH.read_text(encoding="utf-8"),
                      PS1.read_text(encoding="utf-8-sig")):
            assert "--stdin" in metin
            assert "user ensure" in metin or "'user', 'ensure'" in metin

    def test_neither_script_puts_the_password_in_a_command_line(self):
        sh = SH.read_text(encoding="utf-8")
        ps = PS1.read_text(encoding="utf-8-sig")
        # Parola degiskeni yalnizca BORU HATTINDA gecmeli. Ham dizgi:
        # aranan sey dosyadaki iki karakterlik `\n`, gercek satir sonu degil.
        assert r'printf ' + "'%s\\n'" + r' "$pw1" | $LAUNCHER' in sh
        assert '$parola | & $launcher.Exe @argv' in ps
        for metin in (sh, ps):
            assert "--password" not in metin

    def test_the_shell_restores_the_terminal_echo(self):
        """`stty -echo` acikken Ctrl-C yerse kullanici bundan sonra ne
        yazdigini goremez -- terminali bozup birakmak kabul edilemez."""
        sh = SH.read_text(encoding="utf-8")
        assert "stty -echo" in sh
        assert "trap 'stty" in sh, "kesintide yanki geri acilmali"

    def test_both_say_that_typing_is_invisible(self):
        """Ilk sikayet muhtemelen buydu: `getpass` yildiz bile gostermez,
        kullanici yazdiginin gitmedigini sanir."""
        for metin in (SH.read_text(encoding="utf-8"),
                      PS1.read_text(encoding="utf-8-sig")):
            assert "EKRANDA HICBIR SEY GORUNMEZ" in metin

    def test_both_confirm_the_password_twice(self):
        assert "Yeni parola (tekrar)" in SH.read_text(encoding="utf-8")
        assert "Yeni parola (tekrar)" in PS1.read_text(encoding="utf-8-sig")

    def test_the_powershell_frees_the_decrypted_string(self):
        """`SecureStringToBSTR` ile ayrilan bellek elle birakilmali;
        yoksa parola surec belleginde kalir."""
        ps = PS1.read_text(encoding="utf-8-sig")
        assert ps.count("SecureStringToBSTR") == ps.count("ZeroFreeBSTR")

    def test_the_help_mentions_it(self):
        assert "passwd" in SH.read_text(encoding="utf-8").split("Secenekler")[0]
        assert "passwd" in PS1.read_text(encoding="utf-8-sig").split("Secenekler")[0]


class TestTheProjectsOwnKnowledgeBase:
    """DeerX'in kendi belgelerini ve kodunu indeksleyen taban.

    Depo 7.500 satir belge ve 16.000 satir kod tasiyor. "Neden boyle
    yapilmis" sorusunun cevabi cogu zaman bir yorumda ya da bir test
    docstring'inde; grep sozcugu bulur, erisim pasaji bulur.
    """

    BUILD = SCRIPTS / "knowledge" / "build.py"
    ASK = SCRIPTS / "knowledge" / "ask.py"

    def test_both_scripts_exist(self):
        for yol in (self.BUILD, self.ASK):
            assert yol.is_file(), yol

    def test_they_parse(self):
        import ast

        for yol in (self.BUILD, self.ASK):
            ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))

    def test_what_is_indexed_is_an_explicit_list(self):
        """Depo geneli bir tarama, icinde ne oldugunu kimsenin
        sayamadigi bir taban uretir."""
        metin = self.BUILD.read_text(encoding="utf-8")
        assert "KAYNAKLAR = [" in metin
        for beklenen in ("docs", "src/deerx", "tests", "README.md"):
            assert f'"{beklenen}"' in metin, beklenen

    def test_the_indexed_paths_actually_exist(self):
        """Listede olmayan bir yol sessizce atlanir; listede OLAN ama
        depoda olmayan bir yol, tabanin eksik oldugunu gizler."""
        import re

        metin = self.BUILD.read_text(encoding="utf-8")
        blok = metin.split("KAYNAKLAR = [", 1)[1].split("]", 1)[0]
        yollar = re.findall(r'"([^"]+)"', blok)
        assert yollar
        eksik = [y for y in yollar if not (ROOT / y).exists()]
        # `SECURITY.md`/`CONTRIBUTING.md` gibi dosyalar olmayabilir; ama
        # cekirdek dordu olmali.
        cekirdek = {"docs", "src/deerx", "tests", "README.md"}
        assert not (cekirdek & set(eksik)), f"listelenen ama olmayan: {eksik}"

    def test_noisy_files_are_excluded_by_name(self):
        """OLCULDU: `index.html` isaretlemesinden arta kalan sozcukler
        "denetim gunlugu" sorgusunda birinci siraya cikiyordu; konuyu
        gercekten anlatan `security.md` listeye hic giremiyordu."""
        metin = self.BUILD.read_text(encoding="utf-8")
        for gurultu in ("static/index.html", "static/i18n.js", "docs/images"):
            assert gurultu in metin, gurultu

    def test_the_answer_is_grounded_in_the_excerpts(self):
        """Bir belge tabaninin degeri, cevabin nereden geldigini
        gosterebilmesinde. "Bildigim kadariyla" diyen bir cevap, tabanin
        hic sorgulanmamasiyla ayni."""
        metin = self.ASK.read_text(encoding="utf-8")
        assert "YALNIZCA sana verilen alintilara" in metin
        assert "tabanda yok" in metin, "bulunamayan sey soylenmeli"

    def test_sources_are_printed_regardless_of_the_model(self):
        """Model atif yapmayabilir; kullanici yine de nereye bakacagini
        bilmeli."""
        metin = self.ASK.read_text(encoding="utf-8")
        assert "Kaynaklar:" in metin

    def test_context_drops_whole_excerpts_rather_than_truncating(self):
        """Yarim kesilmis bir alinti modele TAM gorunur ve eksik bilgiden
        emin bir cevap uretir."""
        metin = self.ASK.read_text(encoding="utf-8")
        blok = metin.split("def baglam_kur", 1)[1].split("def ", 1)[0]
        assert "break" in blok, "butce asilinca alinti BIRAKILMALI"

    def test_the_knowledge_base_is_not_committed(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        assert ".deerx-kb/" in ignore

    def test_the_documentation_exists_in_both_languages(self):
        for yol in (ROOT / "docs" / "knowledge-base.md",
                    ROOT / "docs" / "tr" / "knowledge-base.md"):
            assert yol.is_file(), yol
