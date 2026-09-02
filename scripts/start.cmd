@echo off
REM DeerX'i baslatir. CIFT TIKLAMAK icin bu dosya.
REM
REM `deerx.cmd` bir komut satiri sarmalayicisidir ve argumansiz
REM cagrildiginda yardim basar. Explorer'dan cift tiklandiginda hicbir
REM arguman gelmez: pencere yardimi gosterip aninda kapanir ve hicbir sey
REM baslamaz -- disaridan bakan biri "tikladim, bir sey olmadi" gorur.
REM
REM Ayrimi `deerx.cmd` icinde tahmin etmek yerine ayri bir dosya:
REM %cmdcmdline% ile cift tiklamayi anlamak, PowerShell'den yapilan
REM cagrilari da cift tiklama sanir ve `deerx.cmd` argumansiz
REM cagrildiginda -- yardim beklenirken -- aga acik bir sunucu baslatirdi.
REM
REM Port, adres ve calisma alani deerx.local.conf'tan gelir (varsa).
setlocal
title DeerX

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deerx.ps1" start
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo Baslatilamadi. Sebebi yukarida yaziyor.
) else (
  echo Sunucu ARKA PLANDA calisiyor; bu pencereyi kapatabilirsiniz.
  echo Durdurmak icin:  scripts\deerx.cmd stop
)
echo.
pause
exit /b %RC%
