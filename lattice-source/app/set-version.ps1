# Set Lattice's version everywhere it is written, from one place (app\VERSION):
#   powershell -ExecutionPolicy Bypass -File app\set-version.ps1 0.2.1
# Without a version, the one in app\VERSION is written to the files (build-release.ps1 does this).
# Numbering: 0.2.0 -> 0.2.1 for fixes, 0.3.0 for new features; a build of the same version is refused.
param([string]$Version = "")
$app = $PSScriptRoot
if (-not $Version) { $Version = (Get-Content "$app\VERSION" -Raw).Trim() }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { Write-Host "Not a version: '$Version' (use 1.2.3)"; exit 1 }
Set-Content -Path "$app\VERSION" -Value $Version -NoNewline -Encoding ascii

# file, pattern of the line holding the version (its first match is replaced)
$places = @(
    @("$app\desktop\package.json", '("version"\s*:\s*")[^"]+(")'),
    @("$app\desktop\src-tauri\tauri.conf.json", '("version"\s*:\s*")[^"]+(")'),
    @("$app\desktop\src-tauri\Cargo.toml", '(?m)(^version\s*=\s*")[^"]+(")'),
    @("$app\backend\pyproject.toml", '(?m)(^version\s*=\s*")[^"]+(")'),
    @("$app\backend\oasis_engine\__init__.py", '(__version__\s*=\s*")[^"]+(")')
)
foreach ($p in $places) {
    $file, $pattern = $p
    $text = [IO.File]::ReadAllText($file)
    $new = ([regex]$pattern).Replace($text, "`${1}$Version`${2}", 1)
    if ($new -ne $text) { [IO.File]::WriteAllText($file, $new, (New-Object Text.UTF8Encoding $false)) }
}
Write-Host "Lattice version $Version"
