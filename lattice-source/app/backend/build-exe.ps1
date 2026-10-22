# Package the engine as a standalone folder: app/runtime/backend-dist/lattice-backend/lattice-backend.exe
# No Python is needed on the target machine. Run from app/backend:  .\build-exe.ps1
# Not "Stop": PyInstaller logs to stderr, which Windows PowerShell 5.1 would treat as a fatal error.
# Success is judged by the exit code below.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$packages = @("pydantic_ai", "uvicorn", "oasis_engine")
# Packages that read their own metadata or data files at import time.
$withMetadata = @("genai_prices", "pydantic_ai_slim", "pydantic_graph", "pydantic", "openai", "fastapi", "starlette", "uvicorn", "httpx", "opentelemetry-api", "logfire-api", "griffelib")
$withData = @("genai_prices")

$pyiArgs = @(
    "run_backend.py", "--name", "lattice-backend", "--onedir", "--console", "--noconfirm", "--clean",
    "--distpath", "..\runtime\backend-dist", "--workpath", "build\pyinstaller", "--specpath", "build",
    "--exclude-module", "pytest", "--exclude-module", "tkinter"
)
foreach ($p in $packages) { $pyiArgs += @("--collect-submodules", $p) }
foreach ($p in $withMetadata) { $pyiArgs += @("--copy-metadata", $p) }
foreach ($p in $withData) { $pyiArgs += @("--collect-data", $p) }

& .\.venv\Scripts\python.exe -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
