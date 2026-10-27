# Build a release installer from the current sources, always in this order:
#   1. engine tests, UI stability tests   2. package the engine (PyInstaller)   3. stage runtime   4. Tauri installer
# Every step rebuilds from source, so the installer can never ship an engine older than the UI.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File app\build-release.ps1
$ErrorActionPreference = "Continue"
$app = $PSScriptRoot
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

# Every installer is kept, by version, in Lattice-share\builds (see set-version.ps1): a version already
# built there is refused, so each build that leaves this machine has a number of its own
$version = (Get-Content "$app\VERSION" -Raw).Trim()
$builds = Join-Path (Split-Path (Split-Path $app)) "Lattice-share\builds"
$kept = Join-Path $builds "Lattice-$version-setup.exe"
if (Test-Path $kept) {
    Write-Host "Version $version was already built ($kept). Set the next one first, for example:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File $app\set-version.ps1 <next version>"
    exit 1
}
# ... and says what changed: the version's section of CHANGELOG.md goes into BUILDS.md with it
$changelog = Get-Content "$app\CHANGELOG.md" -Raw -Encoding UTF8  # (without it, "·" became "Â·" in BUILDS.md)
$section = [regex]::Match($changelog, "(?ms)^## $([regex]::Escape($version))\b[^\n]*\n(.*?)(?=^## |\z)")
if (-not $section.Success -or -not $section.Groups[1].Value.Trim()) {
    Write-Host "CHANGELOG.md has no section for $version. Add '## $version - <date>' with what changed, then build."
    exit 1
}
$changes = $section.Groups[1].Value.Trim()
powershell -NoProfile -ExecutionPolicy Bypass -File "$app\set-version.ps1" | Out-Host

function Step($name, [scriptblock]$run) {
    $t0 = Get-Date
    Write-Host "==> $name"
    & $run
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name (exit $LASTEXITCODE)"; exit 1 }
    Write-Host ("    done in {0:N0}s" -f ((Get-Date) - $t0).TotalSeconds)
}

Step "Engine tests" {
    Push-Location "$app\backend"; & .\.venv\Scripts\python.exe -m pytest -q -p no:warnings; Pop-Location
}
Step "UI stability tests" {
    # layout shift, overflow at every window size, screenshots (Microsoft Edge = the app's WebView2 engine)
    # The result comes from the JSON report: the runner sometimes finishes every test but does not exit,
    # so it is stopped after 10 minutes and judged by what it reported.
    $report = "$env:TEMP\lattice-ui-tests.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    $env:PLAYWRIGHT_JSON_OUTPUT_NAME = $report
    $runner = Start-Process -FilePath "cmd.exe" -WorkingDirectory "$app\desktop" -NoNewWindow -PassThru `
        -ArgumentList "/c", "npx playwright test --reporter=line,json"
    if (-not $runner.WaitForExit(600000)) {
        Write-Host "    (the test runner did not exit; stopping it)"
        function Stop-Tree([int]$id) {
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" | ForEach-Object { Stop-Tree $_.ProcessId }
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
        Stop-Tree $runner.Id
    }
    # a test engine or Vite whose parent is already gone is not in that tree: stop whatever still listens
    # on the test ports (8799 engine, 1431 UI; see playwright.config.ts), or it holds them for the next run
    Get-NetTCPConnection -State Listen -LocalPort 8799, 1431 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    (stopping a leftover test server, pid $($_.OwningProcess))"
                         Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Remove-Item Env:PLAYWRIGHT_JSON_OUTPUT_NAME
    $stats = if (Test-Path $report) { (Get-Content $report -Raw | ConvertFrom-Json).stats } else { $null }
    if ($stats -and $stats.expected -gt 0 -and $stats.unexpected -eq 0 -and $stats.flaky -eq 0) {
        Write-Host "    $($stats.expected) UI tests passed"; $global:LASTEXITCODE = 0
    } else {
        Write-Host "    UI tests failed or did not report: see app\desktop\ui-tests\report\index.html"; $global:LASTEXITCODE = 1
    }
}
Step "Package engine" {
    powershell -NoProfile -ExecutionPolicy Bypass -File "$app\backend\build-exe.ps1" *> "$env:TEMP\oasis-pyinstaller.log"
}
Step "Stage runtime" {
    powershell -NoProfile -ExecutionPolicy Bypass -File "$app\prepare-runtime.ps1"
}
Step "Build installer" {
    Push-Location "$app\desktop"; npm run tauri build 2>&1 | Select-String -Pattern "Finished|error|warning: unused" | ForEach-Object { $_.Line }; Pop-Location
}

$setup = Get-ChildItem "$app\desktop\src-tauri\target\release\bundle\nsis\*-setup.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
$engine = Get-Item "$app\runtime\stage\backend\lattice-backend.exe"
Write-Host ""
Write-Host ("Installer: {0} ({1:N1} MB, {2:HH:mm})" -f $setup.FullName, ($setup.Length / 1MB), $setup.LastWriteTime)
Write-Host ("Engine packaged at {0:HH:mm}; newest engine source change at {1:HH:mm}" -f $engine.LastWriteTime,
    (Get-ChildItem "$app\backend\oasis_engine\*.py" | Sort-Object LastWriteTime | Select-Object -Last 1).LastWriteTime)

# keep it: Lattice-share\builds\Lattice-<version>-setup.exe, and a line in BUILDS.md
$setup = Get-ChildItem "$app\desktop\src-tauri\target\release\bundle\nsis\Lattice_$($version)_x64-setup.exe"
New-Item -ItemType Directory -Force $builds | Out-Null
Copy-Item $setup.FullName $kept
$log = Join-Path $builds "BUILDS.md"
if (-not (Test-Path $log)) {
    Set-Content $log -Encoding utf8 -Value ("# Lattice builds`n`nEach installer is kept here as Lattice-<version>-setup.exe, newest " +
        "last. Each entry says what changed (from CHANGELOG.md in the source).")
}
$repo = Join-Path (Split-Path (Split-Path $app)) "lattice"
$commit = if (Test-Path "$repo\.git") { (git -C $repo log -1 --format="%h %s" 2>$null) } else { "-" }
$entry = "`n## {0} - built {1:yyyy-MM-dd HH:mm}`n`nInstaller: Lattice-{0}-setup.exe ({2:N1} MB)  `n" +
    "Source commit: {3}  `nTests: engine and UI tests passed`n`n{4}"
Add-Content $log -Encoding utf8 -Value ($entry -f $version, $setup.LastWriteTime, ($setup.Length / 1MB), $commit, $changes)
Write-Host "Kept as $kept"
