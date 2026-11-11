# Instala dependencias em um ambiente virtual local (.venv). Requer Python 3.10+ no PATH.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ nao encontrado no PATH." }
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m pip install -e .
Write-Host "Instalacao concluida. Execute scripts\run.ps1"
