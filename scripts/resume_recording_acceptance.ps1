# Resume only the pending recording checks. Never replace historical reports.
[CmdletBinding()]
param(
    [switch]$AllowCloud,
    [string]$Python = "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$fixture = Join-Path $project 'backend/tmp/official-recording-release-2026-10-24'
$output = Join-Path $project ('backend/tmp/recording-resume-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $output -ErrorAction Stop | Out-Null
$results = [System.Collections.Generic.List[object]]::new()
$env:PYTHONPATH = "$project/backend/tmp/stage60-deps;$project/backend;$project/backend/scripts"
$env:PYTHONIOENCODING = 'utf-8'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
$env:PDFAGENT_PUBLIC_GENERATION_FIXTURE = "$fixture/data/fixture.json"
$env:PDFAGENT_ACCEPTANCE_FIXTURE = "$fixture/data"
$env:PDFAGENT_ACCEPTANCE_API = 'http://127.0.0.1:8024'
$env:PDFAGENT_DEMO_URL = 'http://127.0.0.1:3344'
$env:PDFAGENT_GRAPH_EXPECTED = "$output/graph-source/graph-20.json"
$env:PDFAGENT_GRAPH_OUTPUT = "$output/graph-browser"
$env:PDFAGENT_CHART_INPUTS = "$project/backend/tmp/official-uncovered-2026-10-22/csv-font-regression"
$env:PDFAGENT_CHART_OUTPUT = "$output/charts-browser"
function Run-Check([string]$Name, [scriptblock]$Body) {
    $log = Join-Path $output ($Name + '.log')
    & $Body *> $log
    $exit = $LASTEXITCODE
    $results.Add(@{ name = $Name; exit_code = $exit; log = $log })
    Write-Host "$Name exit=$exit"
    return ($exit -eq 0)
}
Push-Location $project
try {
    # Deliberately do not start/rebuild Docker or read secrets here.
    $null = Invoke-WebRequest 'http://127.0.0.1:8024/api/v1/health' -TimeoutSec 15
    $null = Invoke-WebRequest 'http://127.0.0.1:3344/' -TimeoutSec 15
    $apiSourcePassed = Run-Check 'api-source' {
        docker compose -f docker-compose.recording.yml exec -T recording-api python scripts/inspect_runtime_source.py
    }
    $workerSourcePassed = Run-Check 'worker-source' {
        docker compose -f docker-compose.recording.yml exec -T recording-worker python scripts/inspect_runtime_source.py
    }
    $localSourcePassed = Run-Check 'local-source' {
        & $Python backend/scripts/inspect_runtime_source.py
    }
    if (-not ($apiSourcePassed -and $workerSourcePassed -and $localSourcePassed)) {
        throw 'Runtime source inspection failed; no model call was started.'
    }
    $apiSource = Get-Content (Join-Path $output 'api-source.log') -Raw | ConvertFrom-Json
    $workerSource = Get-Content (Join-Path $output 'worker-source.log') -Raw | ConvertFrom-Json
    $localSource = Get-Content (Join-Path $output 'local-source.log') -Raw | ConvertFrom-Json
    if ($apiSource.app_sha256 -ne $localSource.app_sha256 -or $workerSource.app_sha256 -ne $localSource.app_sha256) {
        throw 'API/Worker source differs from the workspace. Rebuild the recording images before live acceptance.'
    }
    $null = Run-Check 'saved-output-audit' {
        & $Python backend/scripts/audit_recording_saved_outputs.py --run $fixture --output "$output/saved-audit.json"
    }
    $graphPassed = Run-Check 'graph-source' {
        & $Python backend/scripts/verify_recording_graph.py --run $fixture --output "$output/graph-source"
    }
    Set-Location (Join-Path $project 'frontend')
    if ($graphPassed) { $null = Run-Check 'graph-browser' { node e2e/officialGraphAcceptance.mjs } }
    $null = Run-Check 'charts-browser' { node e2e/officialChartAcceptance.mjs }
    if ($AllowCloud) {
        $null = Run-Check 'writing-browser' {
            node e2e/generationReadiness.mjs --url http://127.0.0.1:3344 --api http://127.0.0.1:8024/api/v1 --case writing --allow-cloud --output "$output/writing-browser.json"
        }
    }
} catch {
    $results.Add(@{ name = 'prerequisite'; error = $_.Exception.Message })
    Write-Warning $_.Exception.Message
} finally {
    $results | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $output 'run-summary.json') -Encoding utf8
    Pop-Location
    Write-Host "Evidence: $output"
}
if (@($results | Where-Object { $_.error -or $_.exit_code -ne 0 }).Count) { exit 1 }
