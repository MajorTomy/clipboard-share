@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py clipboard_share.py) || (python clipboard_share.py)
pause
