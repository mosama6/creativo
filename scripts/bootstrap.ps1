$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
}

docker compose up -d
python -m uv sync --all-packages --group dev
python -m uv run alembic -c packages/database/alembic.ini upgrade head
Write-Host "Postgres, Redis, and the schema are ready."
