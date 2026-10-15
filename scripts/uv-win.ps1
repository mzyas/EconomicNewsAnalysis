# Run uv from Windows without touching the WSL/Linux .venv in the same folder.
#   .\scripts\uv-win.ps1 sync --group dev
#   .\scripts\uv-win.ps1 run python -m pytest
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv-win'
$env:PYTHONIOENCODING = 'utf-8'
& uv @args
exit $LASTEXITCODE
