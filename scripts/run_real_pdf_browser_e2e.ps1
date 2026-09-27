param(
    [switch]$Gpu,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$reportDir = Join-Path $projectRoot "backend/tmp/stage64-browser"
$reportPath = Join-Path $reportDir "browser-comparison-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()).json"
$backendReportDir = Join-Path $projectRoot "backend/tmp/stage63-real-pdf"
$browserDependency = Join-Path $projectRoot "frontend/node_modules/playwright-core"
$composeArgs = @("compose", "-f", "docker-compose.yml")
if ($Gpu) { $composeArgs += @("-f", "docker-compose.bge-gpu.yml") }
$composeArgs += @("--env-file", ".env", "--env-file", ".env.bge", "--profile", "local-bge")

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $browserDependency)) {
        Push-Location (Join-Path $projectRoot "frontend")
        try {
            & npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "Frontend test dependencies could not be installed." }
        }
        finally { Pop-Location }
    }
    & (Join-Path $PSScriptRoot "run_real_pdf_comparison_e2e.ps1") -Gpu:$Gpu -Build:$Build
    if ($LASTEXITCODE -ne 0) { throw "Real-PDF backend gold gate failed." }

    if ($Build) {
        & docker @composeArgs build frontend
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    }
    & docker @composeArgs up -d frontend
    if ($LASTEXITCODE -ne 0) { throw "Frontend failed to start." }

    $frontendBinding = & docker @composeArgs port frontend 3000 | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or -not $frontendBinding) {
        throw "Could not resolve frontend host port."
    }
    $frontendPort = ($frontendBinding -split ":")[-1]
    $frontendUrl = "http://localhost:$frontendPort"
    $readyDeadline = (Get-Date).AddSeconds(120)
    do {
        try {
            $frontendResponse = Invoke-WebRequest -Uri $frontendUrl -TimeoutSec 5
            if ($frontendResponse.StatusCode -eq 200) { break }
        }
        catch { Start-Sleep -Seconds 2 }
    } while ((Get-Date) -lt $readyDeadline)
    if ($frontendResponse.StatusCode -ne 200) { throw "Frontend health check timed out." }

    $backendReport = Get-ChildItem -LiteralPath $backendReportDir -Filter "*.json" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $backendReport) { throw "The backend gate did not save a report." }
    & node frontend/e2e/realPdfComparison.mjs `
        --url $frontendUrl `
        --backend-report $backendReport.FullName `
        --output $reportPath
    if ($LASTEXITCODE -ne 0) { throw "Browser acceptance failed: $reportPath" }
    Write-Host "Real-PDF browser acceptance passed. Report: $reportPath"
}
finally {
    Pop-Location
}
