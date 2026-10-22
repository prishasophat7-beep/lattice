# Assemble what the installer ships into app/runtime/stage/ (bundled by Tauri as <resources>/runtime/).
#   stage/backend/   the packaged engine (run backend\build-exe.ps1 first)
#   stage/llama/     llama-server.exe, the DLLs it loads, and llama.cpp's license files
# The language model (.gguf) is NOT bundled: it ships next to the installer and is imported in the app.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$stage = Join-Path $root "runtime\stage"
$backend = Join-Path $root "runtime\backend-dist\lattice-backend"
$llama = Join-Path $root "runtime\llama"

if (-not (Test-Path "$backend\lattice-backend.exe")) { throw "Packaged backend missing: run backend\build-exe.ps1 first" }
if (-not (Test-Path "$llama\llama-server.exe")) { throw "llama.cpp missing: expected $llama\llama-server.exe" }

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force "$stage\llama" | Out-Null

Copy-Item $backend "$stage\backend" -Recurse
Copy-Item "$llama\llama-server.exe" "$stage\llama\"
# All shared DLLs, but not the per-tool "*-impl.dll" files of tools we don't ship (bench, cli, ...)
Get-ChildItem $llama -Filter "*.dll" |
    Where-Object { $_.Name -notlike "*-impl.dll" -or $_.Name -eq "llama-server-impl.dll" } |
    Copy-Item -Destination "$stage\llama\"
if (-not (Test-Path "$llama\LICENSE-llama.cpp")) { throw "Missing $llama\LICENSE-llama.cpp (MIT; required when redistributing)" }
Get-ChildItem $llama -Filter "LICENSE*" | Copy-Item -Destination "$stage\llama\"

$mb = (Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
"Staged runtime: {0:N0} MB in {1}" -f $mb, $stage
