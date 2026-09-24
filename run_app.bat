@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
%PY% -c "import streamlit" >nul 2>nul
if not %errorlevel%==0 (
  echo Installing Streamlit...
  %PY% -m pip install -r requirements.txt
)
%PY% -m streamlit run app.py
pause
