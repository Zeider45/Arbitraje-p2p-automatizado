@echo off
REM Arranque sencillo en Windows: crea el entorno, instala dependencias y abre el panel.
cd /d "%~dp0"

if not exist .venv (
  echo Creando entorno virtual...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if not exist config.toml if not "%1"=="--demo" (
  echo No existe config.toml: copio config.example.toml ^(editalo y vuelve a ejecutar^).
  copy config.example.toml config.toml
)

python -m arbitraje %*
