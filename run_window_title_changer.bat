@echo off
cd /d "%~dp0"
python window_title_changer.py
if errorlevel 1 pause
