@echo off

set NODE=WF

rem show launcher: 30s delay lets GPU / network / USB camera initialize after boot
timeout /t 30 /nobreak

cd /d "%~dp0.."
start "" "%programfiles%\Derivative\TouchDesigner\bin\TouchDesigner.exe" "ONAY-Main.toe"
