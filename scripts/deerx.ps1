<#
.SYNOPSIS
    DeerX web sunucusu - baslat / durdur / yeniden baslat.  (Windows)

.DESCRIPTION
    PID ve gunluk calisma alaninin `.deerx/` dizininde tutulur; boylece her
    calisma alani kendi sunucusunu bagimsiz yonetir.

.EXAMPLE
    .\scripts\deerx.ps1 start
    .\scripts\deerx.ps1 restart -Port 9000
    .\scripts\deerx.ps1 stop
    .\scripts\deerx.ps1 status
    .\scripts\deerx.ps1 logs -Follow
#>
# NOT: Bu dosya UTF-8 BOM ile kaydedilmelidir. BOM olmadan Windows
# PowerShell 5.1 dosyayi ANSI sanir; Turkce karakterler ve tire
# isaretleri bozulur, dosya ayristirilamaz hale gelir.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'passwd', 'start', 'stop', 'restart', 'status', 'logs', 'help')]
    [string]$Command = 'help',

    [int]$Port = 8791,
    [string]$BindHost = '127.0.0.1',
    [string]$Workspace = $PWD.Path,
    [string]$Account = 'admin',
    [switch]$Follow
)

$ErrorActionPreference = 'Stop'

$StopGraceSeconds = 8       # TERM yerine zorla kapatiyoruz; bu sure portun
                            # bosalmasini beklemek icin
$StartTimeout     = 90      # ilk acilista gomme modeli yuklenebilir

$Root = Split-Path -Parent $PSScriptRoot

# Yerel varsayilanlar. Bu dosya surum kontrolune GIRMEZ ve bilerek oyle:
# depo herkese acik, varsayilani 0.0.0.0 yapmak klonlayan herkesin
# DeerX'ini aga acardi. Ornegi icin deerx.local.conf.example.
#
# Onceklik: komut satiri > bu dosya > parametre varsayilanlari. Sirayi
# `$PSBoundParameters` kurar: acikca verilmis bir parametreyi ezmiyoruz.
#
# Dosya nokta-kaynak ile yuklenmiyor, SATIR SATIR okunuyor: kaynak almak,
# ayar dosyasina yazilan her seyi PowerShell komutu olarak calistirmakti.
$LocalConf = Join-Path $PSScriptRoot 'deerx.local.conf'
$LocalUsed = @()
if (Test-Path $LocalConf) {
    foreach ($line in (Get-Content -LiteralPath $LocalConf)) {
        $satir = $line.Trim()
        if ($satir -eq '' -or $satir.StartsWith('#')) { continue }
        $ayrac = $satir.IndexOf('=')
        if ($ayrac -lt 1) { continue }
        $anahtar = $satir.Substring(0, $ayrac).Trim()
        $deger   = $satir.Substring($ayrac + 1).Trim().Trim('"')
        switch ($anahtar) {
            'PORT' {
                if (-not $PSBoundParameters.ContainsKey('Port')) {
                    $Port = [int]$deger; $LocalUsed += 'PORT'
                }
            }
            'HOST' {
                if (-not $PSBoundParameters.ContainsKey('BindHost')) {
                    $BindHost = $deger; $LocalUsed += 'HOST'
                }
            }
            'WORKSPACE' {
                if (-not $PSBoundParameters.ContainsKey('Workspace')) {
                    $Workspace = $deger; $LocalUsed += 'WORKSPACE'
                }
            }
            default { Write-Warning "$LocalConf : taninmayan anahtar '$anahtar'" }
        }
    }
}

if (-not (Test-Path $Workspace)) { throw "Calisma alani bulunamadi: $Workspace" }
$Workspace = (Resolve-Path $Workspace).Path

# Yerel dosyadan gelen degerler SOYLENIR. Sessiz kalsaydi biri
# `deerx.ps1 start` yazip sunucunun neden 0.0.0.0'a baglandigini
# anlamazdi -- ve bunu ancak disaridan biri girdiginde fark ederdi.
if ($LocalUsed.Count) {
    Write-Host ("Yerel ayar: {0} -> {1}" -f $LocalConf, ($LocalUsed -join ' '))
}

$DataDir = Join-Path $Workspace '.deerx'
$PidFile = Join-Path $DataDir 'server.pid'
$LogFile = Join-Path $DataDir 'server.log'
$ErrFile = Join-Path $DataDir 'server.err.log'
$Url     = "http://${BindHost}:${Port}"

# Sunucu sureci bu adlardan biriyle calisir. Baska bir ad tasiyan bir surec,
# komut satirinda "deerx" gecse bile bizim sunucumuz degildir.
$OurNames = @('python', 'pythonw', 'deerx', 'uv')

# ── Yardimcilar ─────────────────────────────────────────────────────────── #

function Resolve-Launcher {
    $venv = Join-Path $Root '.venv\Scripts\deerx.exe'
    if (Test-Path $venv) { return @{ Exe = $venv; Pre = @() } }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) { return @{ Exe = $uv.Source; Pre = @('run', '--project', $Root, 'deerx') } }

    $deerx = Get-Command deerx -ErrorAction SilentlyContinue
    if ($deerx) { return @{ Exe = $deerx.Source; Pre = @() } }

    throw "deerx bulunamadi. Kurulum icin:  scripts\deerx.cmd setup"
}

# PID gercekten *bizim* sunucumuz mu? PID'ler geri donusur; yanlis sureci
# oldurmemek icin hem surec adina hem komut satirina bakariz. Yalnizca komut
# satirina bakmak yetmez: bu betigi calistiran kabugun komut satirinda da
# "deerx" gecer, yani kabuk kendini sunucu sanabilirdi.
function Test-OurServer([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    try { $proc = Get-Process -Id $ProcessId -ErrorAction Stop } catch { return $false }
    if ($OurNames -notcontains $proc.ProcessName) { return $false }
    try {
        $line = (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
    } catch {
        $line = $null
    }
    # Komut satiri okunamiyorsa emin olamayiz. Emin olmadigimiz bir sureci
    # oldurmektense durdurma islemini yarim birakip uyarmayi tercih ederiz.
    if ([string]::IsNullOrEmpty($line)) { return $false }
    return $line -like '*deerx*serve*'
}

function Get-RunningPid {
    if (-not (Test-Path $PidFile)) { return 0 }
    $raw = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $parsed = 0
    if (-not [int]::TryParse($raw, [ref]$parsed)) { Remove-Item $PidFile -Force; return 0 }
    if (Test-OurServer $parsed) { return $parsed }
    # Bayat PID dosyasi: surec olmus ya da baska bir surece ait.
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return 0
}

# Portu gercekten dinleyen surec. `deerx.exe` bir sarmalayicidir ve asil
# sunucuyu ayri bir `python.exe` olarak baslatir; Start-Process'in dondugu
# PID sarmalayicinindir. Sarmalayiciyi oldurmek cocugu her zaman kapatmaz —
# port dolu kalir. Bu yuzden PID'i porttan cozeriz.
function Get-PortOwner([int]$OnPort) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $OnPort -State Listen -ErrorAction Stop
        return @($conn)[0].OwningProcess
    } catch {
        return 0
    }
}

# Sunucu tek bir surec degil: olculen zincir `deerx.exe` -> `python.exe` ->
# `python.exe` seklinde ve portu en alttaki tutuyor. Windows bir sureci
# oldururken cocuklarini oldurmez; yalnizca kaydedilen PID'i oldurmek geride
# portu tutan bir yetim birakir. Bu yuzden agacin tamamini toplariz.
function Get-OurTree([int]$RootPid) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $found    = @($RootPid)
    $frontier = @($RootPid)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parent in $frontier) {
            foreach ($child in $all) {
                if ($child.ParentProcessId -eq $parent -and $found -notcontains [int]$child.ProcessId) {
                    $next += [int]$child.ProcessId
                }
            }
        }
        $found += $next
        $frontier = $next
    }
    # ParentProcessId geri donusmus bir PID'e isaret edebilir; agaca alakasiz
    # bir surec karismasin diye her adayi ayrica dogrularz.
    return @($found | Where-Object { Test-OurServer $_ })
}

# Verilen surecin dinledigi port. PID dosyasi calisma alanina ait, port ise
# bir parametre; ikisi uyusmayabilir. Bunu sormadan "zaten calisiyor" demek,
# istenen adresi -- calismadigi halde -- calisiyor gibi gosterirdi.
function Get-ListenPort([int]$ProcessId) {
    if ($ProcessId -le 0) { return 0 }
    try {
        $conn = Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.OwningProcess -eq $ProcessId }
        if ($conn) { return @($conn)[0].LocalPort }
    } catch { }
    return 0
}

function Test-HttpUp([string]$Address = $Url) {
    # `/api/overview` yoklanmaz: kimlik dogrulama acikken 401 doner ve
    # Invoke-WebRequest bunu hata sayar, yani sunucu saglamken "yanit yok"
    # denirdi. `/api/auth/status` bilerek herkese acik (PUBLIC_PATHS) ve
    # statik bir dosya degil - uygulamanin kendisini calistirir.
    try {
        $null = Invoke-WebRequest -Uri "$Address/api/auth/status" -TimeoutSec 3 -UseBasicParsing
        return $true
    } catch {
        # Sunucu bir HTTP durumu dondurduyse ayaktadir; yalnizca baglanti hic
        # kurulamadiysa kapalidir. Boylece bu yol ileride korumaya alinsa
        # bile yoklama dogru cevap verir.
        return $null -ne $_.Exception.Response
    }
}

# ── Komutlar ────────────────────────────────────────────────────────────── #

# Yonetici parolasini kurar ya da sifirlar.
#
# `deerx user passwd` tek basina kullanilamiyor: parolayi `getpass` ile
# soruyor, o da Windows'ta konsolu DOGRUDAN okuyor ve boru hattindaki
# veriyi hic gormuyor -- betikten beslendiginde ciktisiz kilitleniyor.
# `--stdin` bunun icin eklendi.
#
# Parola ARGUMAN olarak gecirilmiyor: arguman Gorev Yoneticisi'nde ve
# `Get-CimInstance Win32_Process` ciktisinda gorunur, PowerShell gecmisine
# yazilir. Boru hatti ikisine de dusmez.
function Read-NewPassword {
    Write-Host ''
    Write-Host 'Parolayi yazarken EKRANDA HICBIR SEY GORUNMEZ - ne harf ne yildiz.'
    Write-Host "Bu normaldir; yazip Enter'a basin."
    Write-Host ''
    while ($true) {
        $ilk  = Read-Host 'Yeni parola' -AsSecureString
        $tekr = Read-Host 'Yeni parola (tekrar)' -AsSecureString
        # SecureString'i duz metne cevirmek icin ayrilan bellek elle
        # birakilmali; aksi halde parola surec belleginde kalir.
        $p1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ilk)
        $p2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tekr)
        try {
            $a = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p1)
            $b = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p2)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p1)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p2)
        }
        if ($a -cne $b) { Write-Host 'Iki parola ayni degil. Tekrar deneyin.'; continue }
        # Uzunluga burada da bakiyoruz: sunucunun reddini gormek icin
        # parolayi iki kez yazdirmak gereksiz bir ceza olurdu.
        if ($a.Length -lt 8) { Write-Host 'Parola en az 8 karakter olmali. Tekrar deneyin.'; continue }
        return $a
    }
}

function Invoke-Passwd {
    $launcher = Resolve-Launcher
    Write-Host "Hesap        : $Account"
    Write-Host "Calisma alani: $Workspace"

    $parola = Read-NewPassword
    $argv = @($launcher.Pre) + @('user', 'ensure', $Account, '--admin', '--stdin')
    $parola | & $launcher.Exe @argv
    $rc = $LASTEXITCODE
    $parola = $null
    if ($rc -ne 0) { throw "Parola ayarlanamadi (cikis kodu $rc)." }
    Write-Host ''
    Write-Host 'Bitti. Acik oturumlarin hepsi dustu; yeniden giris gerekiyor.'
}

function Invoke-Setup {
    # Kurulum mantigi `deerx setup` icinde: tek uygulama, iki betik. Iki
    # dilde ayni adimlari tekrarlamak, biri digerinden saptiginda hangisinin
    # dogru oldugunu belirsiz birakirdi.
    #
    # Bagimliliklar henuz kurulu olmayabilir, o yuzden `uv run` yolu da
    # kabul ediliyor -- `Resolve-Launcher` burada kullanilamaz.
    $venv = Join-Path $Root '.venv\Scripts\deerx.exe'
    if (Test-Path $venv) {
        & $venv setup $Workspace
    }
    elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --project $Root deerx setup $Workspace
    }
    else {
        throw "Once uv kurun: https://docs.astral.sh/uv/  (ya da: pip install -e $Root)"
    }
    exit $LASTEXITCODE
}

function Invoke-Start {
    $existing = Get-RunningPid
    if ($existing -gt 0) {
        $onPort = Get-ListenPort $existing
        if ($onPort -gt 0 -and $onPort -ne $Port) {
            # Bir calisma alani = bir sunucu: ayni SQLite dosyasini iki sunucu
            # arasinda paylastirmak istenmez. Ama istenen portta calistigini
            # soylemek de yanlis olur; gercek adresi veriyoruz.
            Write-Host "Bu calisma alani zaten $onPort portunda calisiyor (PID $existing)."
            Write-Host "      Once durdurun:  stop -Port $onPort"
            exit 1
        }
        Write-Host "Zaten calisiyor (PID $existing) - $Url"
        return
    }

    # PID dosyasi yok ama port dolu olabilir: sunucu bu betik disinda
    # baslatilmis ya da PID dosyasi bayatlamis olabilir. Bunu simdi anlamak,
    # 90 saniye bekleyip "yanit vermedi" demekten iyidir.
    $owner = Get-PortOwner $Port
    if ($owner -gt 0) {
        if (Test-OurServer $owner) {
            if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
            Set-Content -Path $PidFile -Value $owner -Encoding ascii
            Write-Host "Zaten calisiyor (PID $owner) - $Url   (PID dosyasi tazelendi)"
        } else {
            $name = try { (Get-Process -Id $owner -ErrorAction Stop).ProcessName } catch { 'bilinmiyor' }
            Write-Host "HATA: $Port portunu $name kullaniyor (PID $owner)."
            Write-Host "      DeerX hep $Port portunda calisir. Once o programi kapatin:"
            Write-Host "        Stop-Process -Id $owner"
            Write-Host "      (zorunluysa gecici olarak: -Port 9000)"
            exit 1
        }
        return
    }

    $launcher = Resolve-Launcher
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

    Write-Host "Baslatiliyor...  calisma alani: $Workspace"

    # PowerShell 5.1 ArgumentList dizisini tirnaklamadan bosluklarla
    # birlestirir; icinde bosluk olan bir yol (ornegin
    # C:\Users\Ada Lovelace\proje) iki argumana bolunurdu.
    $argv = @($launcher.Pre) + @('serve', '--host', $BindHost, '--port', "$Port")
    $argv = @($argv | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } })

    # Splat kullaniliyor: ters tirnakli satir devami sessizce kirilgandir
    # (devamin ardina dusen tek bir bosluk tum blogu bozar).
    # -RedirectStandardOutput ve -RedirectStandardError ayni dosya olamaz.
    $startArgs = @{
        FilePath               = $launcher.Exe
        ArgumentList           = $argv
        WorkingDirectory       = $Workspace
        WindowStyle            = 'Hidden'
        PassThru               = $true
        RedirectStandardOutput = $LogFile
        RedirectStandardError  = $ErrFile
    }
    $proc = Start-Process @startArgs

    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

    $waited = 0
    while ($waited -lt $StartTimeout) {
        # Sarmalayici cikmis olsa bile asil sunucu ayakta olabilir; once
        # porta bakariz, sonra surecin oldugune hukmederiz.
        if ($proc.HasExited -and (Get-PortOwner $Port) -le 0) {
            Write-Host "Sunucu acilamadi (cikis kodu $($proc.ExitCode)). Gunlugun sonu:"
            if (Test-Path $ErrFile) { Get-Content $ErrFile -Tail 20 }
            if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 }
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            exit 1
        }
        if (Test-HttpUp) {
            $owner = Get-PortOwner $Port
            if ($owner -gt 0 -and (Test-OurServer $owner)) {
                Set-Content -Path $PidFile -Value $owner -Encoding ascii
            } else {
                $owner = $proc.Id
            }
            Write-Host "Hazir - $Url   (PID $owner, gunluk: $LogFile)"
            return
        }
        Start-Sleep -Seconds 1
        $waited++
    }
    Write-Host "Surec ayakta (PID $($proc.Id)) ama $StartTimeout saniyede yanit vermedi."
    Write-Host "Gunluge bakin: $LogFile"
    exit 1
}

function Invoke-Stop {
    $targets = @()

    $serverPid = Get-RunningPid
    if ($serverPid -gt 0) { $targets += Get-OurTree $serverPid }

    # PID dosyasi yoksa ya da bayatsa bile port dolu olabilir: sunucu bu
    # betik disinda baslatilmis olabilir. O durumda "calisan sunucu yok"
    # deyip cikmak, portu tutan sureci gorunmez birakirdi.
    $owner = Get-PortOwner $Port
    if ($owner -gt 0 -and $targets -notcontains $owner) {
        if (Test-OurServer $owner) {
            $targets += Get-OurTree $owner
        } else {
            Write-Host "UYARI: $Url dinleniyor (PID $owner) ama bu surec DeerX degil."
            Write-Host '       Dokunulmadi.'
        }
    }

    $targets = @($targets | Select-Object -Unique)
    if ($targets.Count -eq 0) {
        Write-Host 'Calisan sunucu yok.'
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    Write-Host ("Durduruluyor (PID " + ($targets -join ', ') + ")...")

    # Native `taskkill` kullanilmiyor: PowerShell 5.1'de bir exe'nin stderr'i
    # $ErrorActionPreference='Stop' altinda betigi sonlandirir ve durdurma
    # yarim kalir. Stop-Process bir .NET cagrisidir, bu tuzagi tasimaz.
    #
    # Gizli pencereli bir konsol surecini Windows'ta baska bir konsoldan
    # nazikce kapatmanin guvenilir bir yolu yok (CTRL+C ancak ayni konsola
    # gonderilebilir). Proje hafizasi her yazmada commit edildigi icin zorla
    # kapatmak veri kaybettirmez.
    #
    # Cocuklar once: once ebeveyni oldurmek, portu tutan cocugu yetim birakir.
    [array]::Reverse($targets)
    foreach ($target in $targets) {
        Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
    }

    # Surecin olmesi portun bosalmasiyla ayni an degildir; asil olcut port.
    $waited = 0
    while ($waited -lt ($StopGraceSeconds * 2)) {
        if ((Get-PortOwner $Port) -le 0) { break }
        Start-Sleep -Milliseconds 500
        $waited++
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue

    $lingering = Get-PortOwner $Port
    if ($lingering -gt 0) {
        Write-Host "UYARI: $Url hala dinleniyor (PID $lingering)."
    } else {
        Write-Host 'Durduruldu.'
    }
}

function Invoke-Restart {
    Invoke-Stop
    Invoke-Start
}

function Invoke-Status {
    $serverPid = Get-RunningPid
    $owner = Get-PortOwner $Port

    if ($serverPid -gt 0) {
        # Gercekte dinlenen port yazilir, istenen port degil: ikisi
        # ayrildiginda "calisiyor" satirinin altina yanlis adres dusmesin.
        $onPort = Get-ListenPort $serverPid
        $shown = if ($onPort -gt 0) { "http://${BindHost}:${onPort}" } else { $Url }
        Write-Host "Durum   : calisiyor (PID $serverPid)"
        Write-Host "Adres   : $shown"
        if ($onPort -gt 0 -and $onPort -ne $Port) {
            Write-Host "NOT     : sorulan port $Port, dinlenen port $onPort."
        }
        if (Test-HttpUp $shown) {
            Write-Host 'HTTP    : yanit veriyor'
        } else {
            Write-Host 'HTTP    : YANIT YOK (aciliyor olabilir)'
        }
    } elseif ($owner -gt 0 -or (Test-HttpUp)) {
        # PID dosyasi yok ama adres dinleniyor: sunucu bu betikle
        # baslatilmamis. "Durmus" demek yaniltici olurdu - calisan bir
        # sunucuyu durmus gostermek, insani ikinci bir tane baslatmaya
        # ve "port dolu" hatasina goturur.
        $who = if ($owner -gt 0) { " (PID $owner)" } else { '' }
        Write-Host 'Durum   : durmus (PID dosyasi yok)'
        # Portu tutan sey bizim sunucumuz mu, alakasiz bir program mi? Ikisine
        # ayni cumleyi kurmak yaniltir: birinde "devral" dogru tavsiye,
        # digerinde bizim isimiz olmayan bir sureci kapatmayi onermek olur.
        if ($owner -gt 0 -and -not (Test-OurServer $owner)) {
            Write-Host "DIKKAT  : $Port portunu baska bir program kullaniyor$who."
            Write-Host '          Bu bir DeerX sunucusu degil; dokunulmadi.'
        } else {
            Write-Host "DIKKAT  : $Url dinleniyor$who - sunucu bu betik disinda baslatilmis."
            Write-Host '          Yonetimi devralmak icin:  stop, sonra start.'
        }
    } else {
        Write-Host 'Durum   : durmus'
    }
    # Adres durmus halde de yazilir: yerel ayar dosyasi portu ve adresi
    # gorunmeden degistirebiliyor, yani tek basina "durmus" hangi adres
    # hakkinda konustugunu soylemiyordu.
    Write-Host "Adres   : $Url"
    Write-Host "Alan    : $Workspace"
    Write-Host "Gunluk  : $LogFile"
}

function Invoke-Logs {
    # uvicorn kendi satirlarini stderr'e yazar; yalnizca stdout'a bakmak
    # "Address already in use" gibi acilis hatalarini gizlerdi.
    $seen = $false
    if ((Test-Path $ErrFile) -and (Get-Item $ErrFile).Length -gt 0) {
        Write-Host "--- $ErrFile ---"
        Get-Content $ErrFile -Tail 20
        $seen = $true
    }
    if (Test-Path $LogFile) {
        Write-Host "--- $LogFile ---"
        if ($Follow) {
            Get-Content $LogFile -Tail 40 -Wait
            return
        }
        Get-Content $LogFile -Tail 60
        $seen = $true
    }
    if (-not $seen) { throw "Gunluk yok: $LogFile" }
}

function Invoke-Help {
    Write-Host @'
DeerX web sunucusu

  .\scripts\deerx.ps1 setup      bagimlilik, SearXNG, calisma alani
  .\scripts\deerx.ps1 start      calisma alani = bulundugunuz dizin
  .\scripts\deerx.ps1 stop
  .\scripts\deerx.ps1 restart
  .\scripts\deerx.ps1 status
  .\scripts\deerx.ps1 logs [-Follow]
  .\scripts\deerx.ps1 passwd [-Account admin]   parolayi kur/sifirla

Secenekler
  -Port 9000            baska port
  -BindHost 0.0.0.0     baska adres. Sunucuyu AGA ACAR: en az bir kullanici
                        tanimli olmali, yoksa baslamaz.
  -Workspace .\demo     baska calisma alani
  -Account sarpel       hangi hesap (yalnizca passwd; varsayilan: admin)

Her seferinde ayni secenekleri yazmamak icin scripts\deerx.local.conf
olusturun (ornegi: deerx.local.conf.example). Surum kontrolune girmez.
Onceklik: komut satiri > o dosya > varsayilanlar.

PID ve gunluk calisma alaninin .deerx/ dizininde tutulur.
'@
}

switch ($Command) {
    'setup'   { Invoke-Setup }
    'passwd'  { Invoke-Passwd }
    'start'   { Invoke-Start }
    'stop'    { Invoke-Stop }
    'restart' { Invoke-Restart }
    'status'  { Invoke-Status }
    'logs'    { Invoke-Logs }
    default   { Invoke-Help }
}
