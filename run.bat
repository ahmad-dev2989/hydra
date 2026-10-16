@echo off
title HYDRA System Interface
echo ========================================================
echo Launching HYDRA System Interface
echo Powered by Nebius Token Factory ^& NVIDIA Nemotron
echo ========================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" main.py
) else (
    python main.py
)
if errorlevel 1 pause
