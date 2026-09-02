"""Kabuk komutu calistirma.

Politika iki katmanlidir:
    1. `deny_substrings` — kosulsuz reddedilir (yikici komutlar).
    2. `allow_prefixes`  — bos degilse, komutun bu on eklerden biriyle baslamasi gerekir.
Her calistirma ayrica `approval_mode` kapisindan gecer.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..i18n import t
from ..process import child_env, kill_tree, spawn_flags
from .base import Tool, ToolContext, ToolResult

# Onay ekraninda "zincirlenmis" uyarisini tetikleyen isaretler. Yeni satir
# da buradadir: cok satirli bir komut bash'e verildiginde her satir ayri bir
# komuttur ve kullanici bunu gorerek onaylamalidir.
_CHAIN_TOKENS = ("&&", "||", ";", "|", "`", "$(", "\n")

# Yeni bir komut baslatan operatorler.
_SEPARATORS = {"&&", "||", ";", "|", "&"}
# Ardindan gelen token bir dosya adidir, komut degil.
_REDIRECTS = {">", ">>", "<", "<<", "2>", "2>>"}


# Kabuk anahtar sozcukleri: bunlar komut DEGIL. `if`ten sonraki token
# komuttur; `if`in kendisi degil. Ayrisitirici bunlari komut sayinca
# sartli bir betik yazmak imkansizdi.
_KEYWORDS_BEFORE_COMMAND = frozenset({
    "if", "then", "else", "elif", "do", "while", "until", "!", "{", "(",
    "time", "coproc",
})
# Blok kapatanlar: kendileri komut degil, ardindan da komut gelmez.
_KEYWORDS_BLOCK_END = frozenset({"fi", "done", "esac", "}", ")"})
# Bunlardan sonra SOZCUK LISTESI gelir (degisken adi, desen), komut degil:
# `for x in a b c; do ...` -- `x`, `a`, `b`, `c` calistirilmaz.
_KEYWORDS_WORDLIST = frozenset({"for", "case", "select", "in"})
# Sozcuk listesi burada biter ve komut konumu geri gelir.
_WORDLIST_END = frozenset({"do", "then", "in"})


def _mantiksal_satirlar(command: str) -> list[str]:
    """Komutu, TIRNAK DISINDA kalan yeni satirlardan boler.

    Yeni satir bir komut ayracidir ve politika onu GORMUYORDU. `shlex`
    yeni satiri bosluk sayar, dolayisiyla ikinci satirin ilk sozcugu
    komut degil, birincinin argumani gibi gorunuyordu:

        python -c "print(1)"
        whoami

    burada yalnizca `python` denetleniyordu. Komut cok satirli oldugu
    icin `_needs_real_shell` onu bir betige yazip bash'e veriyor ve bash
    IKI komutu da calistiriyordu.

    OLCULDU: izin listesinde olmayan `whoami` tek basina reddedilirken,
    izinli bir satirin ardina konuldugunda calisti ve ciktisini dondurdu.
    `approval_mode = "auto"` kipinde -- otomasyon icin belgelenen ve MCP
    ornek yapilandirmasinda kullanilan kip -- izin listesi tek bariyerdi.

    Tirnak icindeki yeni satir BOLMEZ: `python -c "import x\\nprint(1)"`
    tek bir komuttur ve bolunmesi mesru kullanimi reddederdi -- bu kod
    tabani cok satirli komut destegini tam da onun icin ekledi.

    Ters bolu KACIS SAYILMAZ: Windows yollari (`C:\\Users\\x`) burada
    olagan ve `shlex` de ayni sebeple `posix=False` kullaniyor. Bunun tek
    etkisi satir devami (`\\` + yeni satir) icin fazladan bolmek, yani
    fazladan denetlemek -- guvenli yon.
    """
    satirlar: list[str] = []
    mevcut: list[str] = []
    tirnak: str | None = None

    for karakter in command:
        if tirnak is not None:
            if karakter == tirnak:
                tirnak = None
            mevcut.append(karakter)
            continue
        if karakter in "\"'":
            tirnak = karakter
            mevcut.append(karakter)
            continue
        if karakter == "\n":
            satirlar.append("".join(mevcut))
            mevcut = []
            continue
        mevcut.append(karakter)

    satirlar.append("".join(mevcut))
    return [s for s in (satir.strip() for satir in satirlar) if s]


def _command_heads(command: str) -> list[str]:
    """Komuttaki her segmentin calistirilabilir adini doner.

    Segment = tirnak disi yeni satirlarla ve kabuk operatorleriyle
    ayrilan her parca. Ikisi de sayilmali: bash ikisini de ayri komut
    olarak calistirir.
    """
    heads: list[str] = []
    for satir in _mantiksal_satirlar(command):
        heads.extend(_satir_headleri(satir))
    return heads


def _satir_headleri(command: str) -> list[str]:
    """Tek bir satirdaki zincirlenmis komutlarin adlari.

    Duz `split(";")` kullanmak tirnak icindeki noktali virgulu de sinir sayardi;
    `python -c "import sys; sys.exit(1)"` gibi tamamen mesru komutlar bu yuzden
    reddedilirdi. `shlex` tirnaklama farkindadir ve operatorleri ayri token yapar.
    """
    try:
        # posix=False: Windows yollarindaki ters bolu isaretleri kacis sayilmaz.
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Kapanmamis tirnak vb. — guvenli tarafta kalip kaba bolmeye don.
        tokens = command.replace("&&", " ; ").replace("||", " ; ").replace("|", " ; ").split()

    heads: list[str] = []
    expect_head = True
    skip_next = False
    # `for x in a b c` icindeyiz: bu sozcukler calistirilmaz.
    in_wordlist = False

    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _REDIRECTS:
            skip_next = True
            continue
        if token in _SEPARATORS:
            expect_head = True
            in_wordlist = False
            continue

        kelime = token.strip("\"'").lower()

        if in_wordlist:
            # Liste `do` / `then` ile biter; oradan sonrasi yine komut.
            if kelime in _WORDLIST_END and kelime != "in":
                in_wordlist = False
                expect_head = True
            continue

        if kelime in _KEYWORDS_WORDLIST:
            in_wordlist = True
            continue
        if kelime in _KEYWORDS_BEFORE_COMMAND:
            # Anahtar sozcugun kendisi komut degil; sonraki token komut.
            expect_head = True
            continue
        if kelime in _KEYWORDS_BLOCK_END:
            continue

        if expect_head:
            # Cevre degiskeni on eki (VAR=deger komut) komut adi degildir.
            if "=" in token and not token.startswith(("-", "/", ".")):
                continue
            heads.append(os.path.basename(token.strip("\"'")).lower())
            expect_head = False

    return heads



# Cok satirli komutlar Windows'ta SESSIZCE yarim calisiyordu. `shell=True`
# cmd.exe'yi cagirir; cmd yeni satiri komut sonu sayar, ilk satiri calistirir
# ve GERIYE KALANI ATAR -- ustelik cikis kodu 0 doner.
#
# Olculdu:
#   python -c "\nimport json\nprint(...)\n"   -> cikis 0, cikti YOK
#   echo bir\necho iki                          -> cikis 0, yalnizca "bir"
#
# Basarisizlik gorunmez oldugu icin pahali: alti saatlik bir boru hatti
# kosusunda QA, inceleme ve staging fazlari tur butcelerini bu yuzden
# tuketti. Ajanlarin kendi teshisi kayitlarda duruyor: "Kabuk cok satirli
# komutlari bozuyor. Probe dosyasi yazip onu calistiriyorum."
#
# POSIX kabuklari yeni satiri dogru isler; sorun yalnizca cmd.exe'de. Bash
# varsa (Git ile birlikte gelir) komut ona verilir, yoksa sessizce yarim
# calistirmak yerine ne yapilacagini soyleyen bir hata donulur.
def _posix_shell() -> str | None:
    """Cok satirli komutu dogru calistirabilecek bir kabuk."""
    if os.name != "nt":
        return None          # /bin/sh zaten dogru calisiyor
    return shutil.which("bash") or shutil.which("sh")


# `cmd.exe` bunlari anlamaz; POSIX kabuk sart.
#   ;      -> cmd icin ayrac degil, argumandir
#   $? $(  -> cmd'de %ERRORLEVEL% ve baska bir soz dizimi var
#   `...`  -> komut ikamesi yok
#   '...'  -> cmd tek tirnagi tirnak saymaz
_POSIX_ISARETLERI = (";", "$?", "$(", "`", "'")


def _needs_real_shell(command: str) -> bool:
    """Komut POSIX kabuk gerektiriyor mu? (Yalnizca Windows'ta anlamli.)

    Olculdu: `echo bir; echo iki` komutunda politika UC komut goruyor ama
    `cmd.exe` TEK komut calistirip gerisini arguman yapiyor. Politikanin
    gordugu ile calisanin ayrilmasi, bu kod tabaninin defalarca duzelttigi
    hata bicimi.

    Yalnizca POSIX sozdizimi iceren komutlar yonlendiriliyor: `dir` ve
    `type` cmd.exe YERLESIGIDIR ve POSIX kabukta calismaz.
    """
    if os.name != "nt":
        return False
    metin = command.strip()
    if "\n" in metin:
        return True
    return any(isaret in metin for isaret in _POSIX_ISARETLERI)


def _bare_command(pattern: str) -> bool:
    """Desen bir komut ADI mi, yoksa metinde aranacak bir kalip mi?

    Yasakli desenler ham metinde araniyordu ve bu, ajanin gunluk isini
    engelliyordu. Olculdu: `shutdown` deseni yuzunden `srv.shutdown()`,
    `sock.shutdown(socket.SHUT_RDWR)` ve `--shutdown-timeout` reddedildi --
    HTTP sunucusu yazan biri icin bunlar sirandan API cagrilari. `reboot`
    deseni ise `print('reboot notu')` komutunu engelledi. Yedi ornekten
    dordu yanlis alarmdi ve her biri ajanin bir turunu yakti.

    Ciplak komut adlari (mkfs, shutdown, reboot) artik yalnizca komut
    konumunda aranir; cok kelimeli ya da yol iceren desenler
    (`rm -rf /`, `/dev/sda`, `curl | sh`) ham metinde aranmaya devam eder --
    onlar zaten komut adi degil, tehlikeli KALIPLAR.
    """
    if not pattern or " " in pattern or "/" in pattern or "\\" in pattern:
        return False
    return pattern.replace(".", "").replace("-", "").replace("_", "").isalnum()

def check_command(policy: Any, command: str, *, yalitilmis: bool = False) -> str:
    """Komutu kabuk politikasindan gecirir; temizlenmis halini doner.

    Hem `run_command` hem `start_service` buradan gecer: uzun omurlu bir
    surec baslatmak, tek seferlik bir komut calistirmaktan daha az tehlikeli
    degil.

    `yalitilmis` -- komut bir konteynerde kosacaksa IZIN LISTESI uygulanmaz.
    Liste konagi korumak icin var; konteynerde koruyacak konak yok, geriye
    yalnizca ajanin mesru islerini engellemesi kalir. Olculdu: yalitilmis
    ortamda bile `uname`, `pwd` ve `rm` reddediliyordu -- ajanin yanlislikla
    yarattigi bir dosyayi silememesinin sebebi tam olarak buydu.

    YASAK KALIPLAR yine uygulanir. Calisma alani konteynere BAGLI oldugu
    icin yalitim tam degildir: `rm -rf /` konteyneri de, baglanan gercek
    calisma alanini da siler. Konak korunur, kullanicinin projesi korunmaz --
    o yuzden felaket kaliplari her iki kipte de reddedilir.
    """
    if not policy.enabled:
        raise ToolError(t("shell.disabled"))

    command = command.strip()
    if not command:
        raise ToolError(t("shell.empty"))

    lowered = command.lower()
    heads = {h.split(".")[0] for h in _command_heads(command) if h}
    for banned in policy.deny_substrings:
        needle = banned.strip().lower()
        if _bare_command(needle):
            # Ciplak bir komut adi yalnizca KOMUT KONUMUNDA aranir.
            if needle.split(".")[0] in heads:
                raise ToolError(t("shell.denied_command", pattern=banned))
        elif needle in lowered:
            raise ToolError(t("shell.denied_pattern", pattern=banned))

    if policy.allow_prefixes and not yalitilmis:
        allowed = {p.lower() for p in policy.allow_prefixes}
        unknown = [w for w in _command_heads(command) if w and w.split(".")[0] not in allowed]
        if unknown:
            raise ToolError(
                t("shell.not_allowed", names=", ".join(sorted(set(unknown))))
            )
    return command


class RunCommand(Tool):
    name = "run_command"
    description = """
    Calisma alaninda bir kabuk komutu calistirir ve stdout/stderr doner.
    Testleri kosmak, bagimlilik kurmak, derlemek ve git islemleri icin kullanin.
    Etkilesimli (girdi bekleyen) komutlar calismaz. Uzun surecek komutlar icin
    `timeout` degerini yukseltin.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Calistirilacak komut."},
            "cwd": {
                "type": "string",
                "description": "Calisma dizini (calisma alanina gore, varsayilan kok).",
            },
            "timeout": {"type": "integer", "description": "Saniye cinsinden zaman asimi."},
        },
        "required": ["command"],
    }

    def run(
        self,
        ctx: ToolContext,
        command: str,
        cwd: str = ".",
        timeout: int | None = None,
    ) -> ToolResult:
        policy = ctx.settings.shell
        command = check_command(
            policy, command, yalitilmis=ctx.settings.execution == "docker"
        )

        workdir = ctx.resolve_path(cwd, must_exist=True)
        if not workdir.is_dir():
            raise ToolError(t("shell.not_a_dir", path=cwd))

        risky = any(token in command for token in _CHAIN_TOKENS)
        ctx.approve(
            t("shell.approve", command=command[:160]),
            t(
                "shell.approve_detail",
                cwd=ctx.relative(workdir),
                chained=t("shell.chained") if risky else "",
            ),
            # Her farkli komut ayri onay ister; ayni komut tekrar sorulmaz.
            signature=f"shell:{command}",
        )

        limit = timeout or policy.timeout_seconds
        ctx.events.emit("tool", "shell", t("shell.run", command=command[:140]))
        try:
            if ctx.settings.execution == "docker":
                # Yalitilmis ortamda komut konagi hic gormez: `rm` de calisir,
                # paket de kurulur, surec de oldurulur -- patlama yaricapi
                # konteynerdir. Izin listesi yine de uygulanir; yalitim onun
                # YERINE degil, USTUNE gelir.
                sonuc = _sandbox(ctx).run(command, timeout=limit, workdir=workdir)
                code, stdout, stderr, timed_out = (
                    sonuc.returncode, sonuc.stdout, sonuc.stderr, sonuc.timed_out
                )
            else:
                code, stdout, stderr, timed_out = _run_with_timeout(command, workdir, limit)
        except OSError as exc:
            raise ToolError(t("shell.start_failed", error=exc)) from exc

        if timed_out:
            raise ToolError(
                t(
                    "shell.timeout",
                    seconds=limit,
                    output=(
                        t("shell.partial_output", output=stdout[-2000:])
                        if stdout.strip()
                        else ""
                    ),
                )
            )

        parts = [f"exit_code: {code}{_cikis_kodu_notu(code)}"]
        if stdout.strip():
            parts.append(f"--- stdout ---\n{stdout.rstrip()}")
        if stderr.strip():
            parts.append(f"--- stderr ---\n{stderr.rstrip()}")
        if len(parts) == 1:
            parts.append(t("shell.no_output"))

        return ToolResult(
            content="\n".join(parts),
            # Sifir olmayan cikis kodu modele hata olarak bildirilir ki duzeltmeyi denesin.
            is_error=code != 0,
            data={"returncode": code},
        )


# Windows'ta bir surec olumcul bir NTSTATUS ile dustugunde cikis kodu o
# durumun isaretsiz halidir. Model icin bunlar anlamsiz buyuk sayilar.
_NTSTATUS_NOTLARI = {
    3221225786: "shell.exit_ctrl_c",  # 0xC000013A
    3221225477: "shell.exit_access_violation",  # 0xC0000005
    3221225725: "shell.exit_stack_overflow",  # 0xC00000FD
    3221226505: "shell.exit_buffer_overrun",  # 0xC0000409
    3221225794: "shell.exit_dll_init",  # 0xC0000142
}


def _cikis_kodu_notu(code: int) -> str:
    """Ciplak cikis kodunun yanina insanin okuyabilecegi bir not.

    OLCULDU (gercek kosu): ajanin komutu bir Ctrl+Break olayiyla oldu ve
    modele yalnizca `exit_code: 3221225786` gitti. Model bunu kendi kodundaki
    bir hata sanip orada aramaya basladi; oysa 0xC000013A "konsol denetim
    olayiyla sonlandirildi" demek ve harness bunu biliyordu.

    POSIX'te `Popen.returncode` sinyalle olen surecler icin negatiftir.
    """
    if code in _NTSTATUS_NOTLARI:
        return f" ({t(_NTSTATUS_NOTLARI[code])})"
    if code < 0:
        try:
            ad = signal.Signals(-code).name
        except ValueError:  # pragma: no cover - bilinmeyen sinyal numarasi
            ad = str(-code)
        return f" ({t('shell.exit_signal', signal=ad)})"
    return ""


def _sandbox(ctx: ToolContext):
    """Kosuya ait konteyner; ilk komutta kurulur, sonra yeniden kullanilir."""
    from ..sandbox import Sandbox

    mevcut = getattr(ctx, "_sandbox", None)
    if mevcut is None:
        mevcut = Sandbox(
            workspace=ctx.settings.workspace,
            image=ctx.settings.sandbox_image,
            port_base=ctx.settings.sandbox_port_base,
            port_count=ctx.settings.sandbox_port_count,
            memory=ctx.settings.sandbox_memory,
            cpus=ctx.settings.sandbox_cpus,
            pids_limit=ctx.settings.sandbox_pids,
            setup=ctx.settings.sandbox_setup,
        )
        mevcut.ensure()
        ctx._sandbox = mevcut  # noqa: SLF001 - baglami tasiyan tek yer
    return mevcut


def _run_with_timeout(
    command: str, workdir: Path, limit: int
) -> tuple[int, str, str, bool]:
    """Komutu calistirir; zaman asiminda TUM surec agacini oldurur.

    `subprocess.run(shell=True, timeout=...)` yeterli degildir: zaman asiminda
    yalnizca kabuk oldurulur, onun baslattigi torun surecler yasamaya devam eder
    ve boru uclarini acik tutar. `communicate()` o borulari beklediginden cagri
    komutun kendi suresi kadar bloke olur — zaman asimi fiilen calismaz.
    Olculdu: 2 saniyelik sinirla 30 saniyelik bir komut 30 saniye surdu.

    Cozum: kabugu kendi surec grubunda baslatip zaman asiminda grubun tamamini
    oldurmek.
    """
    script: Path | None = None
    if _needs_real_shell(command):
        shell = _posix_shell()
        if shell is None:
            raise ToolError(
                t("shell.no_multiline_shell")
            )
        # Komut bir dosyaya yazilir: boylece yeni satirlar ve tirnaklar
        # kabuga oldugu gibi ulasir, komut satiri kacislarindan gecmez.
        script = workdir / f".deerx-cmd-{os.getpid()}-{int(time.time() * 1000)}.sh"
        script.write_text(command, encoding="utf-8", newline="\n")
        argv: list[str] | str = [shell, str(script)]
        use_shell = False
    else:
        argv = command
        use_shell = True

    try:
        process = subprocess.Popen(  # noqa: S602 - politika + onay kapisindan gecti
            argv,
            shell=use_shell,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
            **spawn_flags(),  # type: ignore[arg-type]
        )
    except OSError:
        if script is not None:
            script.unlink(missing_ok=True)
        raise
    try:
        try:
            stdout, stderr = process.communicate(timeout=limit)
            return process.returncode, stdout, stderr, False
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            # Agac olduruldugu icin borular kapanir ve bu cagri artik asili kalmaz.
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - agac olmedi
                stdout, stderr = "", ""
            return -1, stdout, stderr, True
    finally:
        if script is not None:
            script.unlink(missing_ok=True)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Sureci ve tum alt sureclerini oldurur."""
    kill_tree(process.pid)
    try:
        process.kill()
    except OSError:  # pragma: no cover - zaten olmus
        pass


SHELL_TOOLS: list[Tool] = [RunCommand()]
