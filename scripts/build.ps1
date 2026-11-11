# Gera dist\TechDiag.exe. Coloque antes RAMMap64.exe, BlueScreenView.exe e speedtest.exe em tools\.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Testes falharam; build cancelado." }
foreach ($t in "RAMMap64.exe","BlueScreenView.exe","speedtest.exe","HWiNFO64.exe") {
  if (-not (Test-Path "tools\$t") -and -not (Get-ChildItem tools -Recurse -Filter $t -ErrorAction SilentlyContinue)) {
    Write-Warning "tools\$t nao encontrado: a funcao correspondente ficara indisponivel ate o arquivo ser adicionado ao lado do .exe (pasta tools)."
  }
}
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean techdiag.spec
Write-Host "Executavel: dist\TechDiag.exe"
