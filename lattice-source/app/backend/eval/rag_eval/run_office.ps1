<#
Lattice RAG eval on the office machine: the installed Lattice engine answering with a network model
(Qwen 3.6 27B), plus retrieval benchmarks with the embedding model Lattice already uses.

  powershell -ExecutionPolicy Bypass -File run_office.ps1 -Endpoint http://<server>:8000/v1 -Model <name as the server lists it>

Set the API key first, if the server needs one (never printed; like Lattice itself, the engine keeps it
in its settings file, here work\engine-data\config.json: delete the work folder when done):
  $env:QWEN_API_KEY = "<key>"

Options:
  -Steps      retrieval, samples, public, judge, report (default: all of them)
  -Sets       retrieval datasets (default samples,emanual,techqa,multihop,scifact,nfcorpus; add fiqa
              for the 57,638-document set: several hours of indexing on a laptop CPU)
  -Limit      questions per public dataset in the end-to-end run (default 60)
  -Judge      judge answers with the same endpoint (default on; a model grading itself is lenient,
              so read faithfulness as an upper bound)
Results: app\backend\eval\rag_eval\results (summary.md is the overview). Rerun to continue: finished
questions are skipped.
#>
param(
  [Parameter(Mandatory = $true)][string]$Endpoint,
  [Parameter(Mandatory = $true)][string]$Model,
  [string]$ApiKeyEnv = "QWEN_API_KEY",
  [string]$Steps = "retrieval,samples,public,judge,report",
  [string]$Sets = "samples,emanual,techqa,multihop,scifact,nfcorpus",
  [int]$Limit = 60,
  [string]$LatticeDir = "C:\Program Files\Lattice",
  [string]$EmbedModel = "",
  [string]$Only = "",   # comma-separated question ids, for a quick check of the setup
  [string]$Python = ""  # an existing python.exe with the engine installed (skips creating .venv-eval)
)
$ErrorActionPreference = "Stop"
$kit = $PSScriptRoot
$backend = (Resolve-Path "$kit\..\..").Path            # app\backend: the engine source and the eval
$venv = "$backend\.venv-eval"
$py = if ($Python) { $Python } else { "$venv\Scripts\python.exe" }
$work = "$kit\work"; New-Item -ItemType Directory -Force $work | Out-Null
$token = [guid]::NewGuid().ToString("N")
$embedKey = [guid]::NewGuid().ToString("N")
$enginePort = 8871; $embedPort = 8196
$want = $Steps.Split(",") | ForEach-Object { $_.Trim() }
function Step($text) { Write-Host ("`n==> {0}  ({1:HH:mm})" -f $text, (Get-Date)) }
function Stop-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
function Wait-Http($url, $key, $seconds = 300) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    try { $r = Invoke-WebRequest $url -Headers @{ Authorization = "Bearer $key" } -UseBasicParsing -TimeoutSec 5
          if ($r.StatusCode -eq 200) { return $true } } catch { }
    Start-Sleep 2
  }
  return $false
}

# ── Python for the eval scripts (the engine itself is the installed lattice-backend.exe) ──
Step "Python environment"
if (-not (Test-Path $py)) {
  $base = (Get-Command py -ErrorAction SilentlyContinue)
  if ($base) { & py -3 -m venv $venv } else { & python -m venv $venv }
  if (-not (Test-Path $py)) { throw "Python 3.11+ is needed: install it from python.org, then run this again." }
  & $py -m pip install --quiet --upgrade pip
  & $py -m pip install --quiet -e $backend
  if ($LASTEXITCODE -ne 0) { throw "pip install failed (the machine needs internet access for this step)" }
}
$env:PYTHONIOENCODING = "utf-8"

# ── embedding model: the one imported into Lattice, unless given ──
if (-not $EmbedModel) {
  $EmbedModel = Get-ChildItem "$env:LOCALAPPDATA\com.oasis.lineassistant\embed-models\*.gguf" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
}
$llama = "$LatticeDir\runtime\llama\llama-server.exe"
$embedUrl = $null
if ($EmbedModel -and (Test-Path $llama)) {
  Step "Embedding server ($([IO.Path]::GetFileName($EmbedModel)))"
  Stop-Port $embedPort
  Start-Process -FilePath $llama -WorkingDirectory (Split-Path $llama) -WindowStyle Hidden `
    -RedirectStandardOutput "$work\embed.log" -RedirectStandardError "$work\embed.err.log" `
    -ArgumentList @("-m", "`"$EmbedModel`"", "--host", "127.0.0.1", "--port", $embedPort, "--api-key", $embedKey,
                    "-np", "1", "--no-webui", "--embeddings", "-c", "2048", "-b", "2048", "-ub", "2048")
  if (-not (Wait-Http "http://127.0.0.1:$embedPort/health" $embedKey)) { throw "The embedding server did not start; see $work\embed.err.log" }
  $embedUrl = "http://127.0.0.1:$embedPort/v1"
  $embedName = [IO.Path]::GetFileNameWithoutExtension($EmbedModel)
} else {
  Write-Host "No embedding model found (import one in Lattice: Manage > Search by meaning). Keyword search only."
}

try {
  if ($want -contains "retrieval") {
    Step "Retrieval benchmarks: $Sets"
    $args_ = @("$kit\retrieval_eval.py", "--sets", $Sets, "--label", "office")
    if ($embedUrl) { $args_ += @("--embed-url", $embedUrl, "--embed-key", $embedKey, "--embed-model", $embedName) }
    & $py @args_
  }

  if ($want -contains "samples" -or $want -contains "public") {
    Step "Lattice engine with $Model"
    Stop-Port $enginePort
    $env:OASIS_DATA_DIR = "$work\engine-data"; $env:OASIS_PORT = "$enginePort"; $env:OASIS_API_TOKEN = $token
    if ($embedUrl) { $env:OASIS_EMBED_BASE_URL = $embedUrl; $env:OASIS_EMBED_API_KEY = $embedKey }
    Start-Process -FilePath "$LatticeDir\runtime\backend\lattice-backend.exe" -WorkingDirectory "$LatticeDir\runtime\backend" `
      -WindowStyle Hidden -RedirectStandardOutput "$work\engine.log" -RedirectStandardError "$work\engine.err.log"
    if (-not (Wait-Http "http://127.0.0.1:$enginePort/api/health" $token)) { throw "The engine did not start; see $work\engine.err.log" }
    # the network model, set the way Manage sets it (office profile, thinking off)
    $key = [Environment]::GetEnvironmentVariable($ApiKeyEnv)
    $config = @{ profile = "office"; default_mode = "manuals"; work_topics_only = $false
                 connection = @{ kind = "remote"; base_url = $Endpoint; model = $Model; api_key = $key; output_mode = "native"; reasoning = $false } } |
              ConvertTo-Json -Depth 4
    Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:$enginePort/api/config" -Headers @{ Authorization = "Bearer $token" } `
      -ContentType "application/json" -Body $config | Out-Null
    $e2e = @("$kit\e2e_eval.py", "--port", $enginePort, "--token", $token)
    if ($embedUrl) { $e2e += "--semantic" }
    if ($Only) { $e2e += @("--only", $Only) }
    if ($want -contains "samples") {
      Step "End to end: sample knowledge bases, Manuals mode"; & $py @e2e --mode manuals --set samples --label office-samples-manuals
      Step "End to end: sample knowledge bases, Chat mode";    & $py @e2e --mode chat --set samples --label office-samples-chat
    }
    if ($want -contains "public") {
      foreach ($set in "emanual", "techqa", "multihop") {
        Step "End to end: $set ($Limit questions), Manuals mode"
        & $py @e2e --mode manuals --set $set --limit $Limit --label "office-$set-manuals"
      }
    }
  }

  if ($want -contains "judge") {
    Step "Judging answers with $Model"
    $labels = Get-ChildItem "$kit\results\e2e-office-*.json" | ForEach-Object { $_.BaseName }
    if ($labels) {
      & $py "$kit\judge.py" @labels --base-url $Endpoint --model $Model --api-key-env $ApiKeyEnv --no-thinking --max-calls 2000
    }
  }
} finally {
  Stop-Port $enginePort; Stop-Port $embedPort
}

if ($want -contains "report") {
  Step "Report"
  & $py "$kit\report.py" | Out-Null
  Write-Host "Done. Overview: $kit\results\summary.md"
}
