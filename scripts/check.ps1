# Yerel denetim: lint + testler. Deponun TEK dogrulamasi.
#
# GitHub Actions is akisi 2026-09-02'de kaldirildi: denetim bu makinede
# kalir, GitHub'a hicbir sey yansimaz. Once alti ortam kosuluyordu
# (Ubuntu/Windows/macOS x Python 3.11/3.13); artik kosulan ne varsa
# burada kosuyor.
#
#   .\scripts\check.ps1            lint + tum suit (bu Python)
#   .\scripts\check.ps1 -Fast      lint + surec baslatmayan testler
#   .\scripts\check.ps1 -Pythons   ustune 3.11 ve 3.13'te de suit
#
# `-Pythons` kaybedilen seyin YARISINI geri getirir: surum farklari.
# Isletim sistemi farkini bir makine veremez, ve o fark gercek --
# Windows'a ozgu bir cikis kodu bekleyen tek bir test, Linux ve macOS'ta
# on bes kosu boyunca kirmizi kaldi, bu makinede hep yesil gorundu.
param([switch]$Fast, [switch]$Pythons)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

# CI'nin kosturdugu surumler. Ana ortam bunlardan biri olabilir; yine de
# ayri kosulur, cunku ana ortamda BUTUN ekler kurulu ve eksik bir ekle
# bozulan bir sey orada gorunmez.
$Surumler = @('3.11', '3.13')

Write-Output '== ruff check src tests'
uv run --no-sync ruff check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Fast) {
    Write-Output ''
    Write-Output '== pytest -q (hizli: gercek surec baslatan testler haric)'
    uv run --no-sync pytest -q -m 'not slow'
} else {
    Write-Output ''
    Write-Output '== pytest -q'
    uv run --no-sync pytest -q
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Pythons) {
    foreach ($surum in $Surumler) {
        # Yan ortam: ana `.venv` hic dokunulmadan kalir. `--extra dev`,
        # eski CI ile ayni -- gomme ve tarayici ekleri olmadan da kosmali.
        $ortam = ".venv-check-$surum"
        Write-Output ''
        Write-Output "== Python $surum (yan ortam: $ortam)"
        $onceki = $env:UV_PROJECT_ENVIRONMENT
        $env:UV_PROJECT_ENVIRONMENT = $ortam
        try {
            uv sync --quiet --extra dev --python $surum
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            uv run --no-sync pytest -q
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally {
            # Degisken geri alinmazsa bu kabuktaki sonraki her `uv`
            # cagrisi yan ortama gider -- sessiz ve kafa karistirici.
            if ($null -eq $onceki) {
                Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
            } else {
                $env:UV_PROJECT_ENVIRONMENT = $onceki
            }
        }
    }
}

Write-Output ''
Write-Output 'Tamam. Bu, deponun tek dogrulamasi.'
if ($Pythons) {
    Write-Output ("Python " + ($Surumler -join ', ') + " kosuldu. Isletim sistemi")
    Write-Output 'farkini bir makine veremez; yol, surec ve kabuk davranisina'
    Write-Output 'dokunan degisikliklerde dikkat.'
} else {
    Write-Output 'Yalnizca bu Python surumunu kapsar; surumler arasi farklar icin:'
    Write-Output '  .\scripts\check.ps1 -Pythons'
}
