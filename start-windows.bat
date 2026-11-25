@echo off
cd /d "%~dp0"
set PY=python
set PYW=pythonw
where py >nul 2>nul && (set PY=py& set PYW=pyw)
echo Installing tray icon support (pystray, Pillow)...
%PY% -m pip install --user -q --disable-pip-version-check -r requirements.txt
%PY% -c "import pystray, PIL" >nul 2>nul && (start "" %PYW% clipboard_share.py & exit /b)
echo Tray icon unavailable, running in this window. Close it to quit.
%PY% clipboard_share.py
pause
