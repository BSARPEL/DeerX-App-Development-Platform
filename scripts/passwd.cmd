@echo off
REM Yonetici parolasini kurar ya da sifirlar. CIFT TIKLAMAK icin bu dosya.
REM
REM Hic kullanici yoksa ANA yoneticiyi kurar; varsa parolasini degistirir.
REM Calisma alani deerx.local.conf'tan gelir (varsa), yoksa bulundugunuz
REM dizin.
REM
REM Baska bir hesap icin:  scripts\deerx.cmd passwd -Account sarpel
setlocal
title DeerX - parola

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deerx.ps1" passwd
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo Parola ayarlanamadi. Sebebi yukarida yaziyor.
echo.
pause
exit /b %RC%
