@echo off
REM DeerX web sunucusu -- deerx.ps1 icin cmd.exe sarmalayicisi.
REM
REM   scripts\deerx.cmd start
REM   scripts\deerx.cmd restart
REM   scripts\deerx.cmd stop
REM   scripts\deerx.cmd status
REM
REM CIFT TIKLAMAK icin bu dosya degil, start.cmd. Explorer hicbir arguman
REM gecmez; burasi o durumda yardim basar ve pencere aninda kapanir.
REM
REM -ExecutionPolicy Bypass yalnizca bu cagri icindir; makinenin ayarini
REM degistirmez. Boylece PowerShell politikasi kisitliyken de calisir.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deerx.ps1" %*
exit /b %ERRORLEVEL%
