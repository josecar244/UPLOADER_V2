@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -m streamlit run index.py
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        py -3 -m streamlit run index.py
    ) else (
        python -m streamlit run index.py
    )
)

if errorlevel 1 (
    echo.
    echo No se pudo iniciar Streamlit.
    echo Verifica que Python y las dependencias del proyecto esten instaladas.
    pause
)
