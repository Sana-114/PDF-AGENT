param(
    [string]$TransformerV7 = "output/pdf/regression-corpus/1706.03762v7.pdf",
    [string]$TransformerV1 = "output/pdf/regression-corpus/1706.03762v1.pdf",
    [int]$TimeoutSeconds = 300,
    [int]$FrontendHostPort = 4300,
    [switch]$KeepDocuments
)

$ErrorActionPreference = "Stop"

function Get-AvailableTcpPort {
    param([int]$StartPort)

    for ($port = $StartPort; $port -lt ($StartPort + 200); $port++) {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Any,
            $port
        )
        try {
            $listener.Server.ExclusiveAddressUse = $true
            $listener.Start()
            return $port
        }
        catch {
            continue
        }
        finally {
            $listener.Stop()
        }
    }
    throw "No available frontend host port found from $StartPort."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceV7 = (Resolve-Path (Join-Path $projectRoot $TransformerV7)).Path
$sourceV1 = (Resolve-Path (Join-Path $projectRoot $TransformerV1)).Path
$previousFrontendHostPort = $env:FRONTEND_HOST_PORT
$previousFrontendOrigin = $env:FRONTEND_ORIGIN
$selectedFrontendHostPort = Get-AvailableTcpPort -StartPort $FrontendHostPort
$env:FRONTEND_HOST_PORT = [string]$selectedFrontendHostPort
$env:FRONTEND_ORIGIN = "http://localhost:$selectedFrontendHostPort"
$workDir = Join-Path $projectRoot "backend/tmp/e2e"
$reportPath = Join-Path $workDir "system-workflow-report.json"
$screenshotPath = Join-Path $workDir "frontend-e2e.png"
$profilePath = Join-Path $workDir "edge-profile-$PID"
$edgeCandidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)

New-Item -ItemType Directory -Force -Path $workDir | Out-Null
Remove-Item -LiteralPath $reportPath -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $screenshotPath -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $sourceV7 -Destination (Join-Path $workDir "source-v7.pdf") -Force
Copy-Item -LiteralPath $sourceV1 -Destination (Join-Path $workDir "source-v1.pdf") -Force

Push-Location $projectRoot
$createdIds = @()
try {
    docker compose up -d qdrant backend worker frontend
    if ($LASTEXITCODE -ne 0) { throw "Docker services failed to start." }

    docker compose exec -T backend python scripts/evaluate_system_workflow.py `
        /app/tmp/e2e/source-v7.pdf `
        /app/tmp/e2e/source-v1.pdf `
        --frontend-url http://frontend:3000 `
        --timeout-seconds $TimeoutSeconds `
        --output /app/tmp/e2e/system-workflow-report.json `
        --strict
    if ($LASTEXITCODE -ne 0) { throw "System workflow evaluation failed." }

    $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    $createdIds = @($report.created_document_ids)
    $frontendBinding = docker compose port frontend 3000
    if ($LASTEXITCODE -ne 0 -or -not $frontendBinding) {
        throw "Could not resolve the frontend port."
    }
    $frontendPort = ($frontendBinding | Select-Object -First 1).Split(":")[-1]
    $frontendUrl = "http://localhost:$frontendPort/"
    $edge = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($edge) {
        $domProfileArg = "--user-data-dir=" + $profilePath + "-dom"
        $dom = & $edge `
            --headless=new `
            --disable-gpu `
            --no-first-run `
            --virtual-time-budget=7000 `
            $domProfileArg `
            --dump-dom `
            $frontendUrl
        $expectedArxivId = [regex]::Escape([string]$report.metrics.arxiv_id)
        if ($dom -notmatch $expectedArxivId) {
            throw "Headless browser did not render the parsed document card."
        }

        Remove-Item -LiteralPath $screenshotPath -ErrorAction SilentlyContinue
        $screenshotProfileArg = "--user-data-dir=" + $profilePath + "-screenshot"
        $screenshotArg = "--screenshot=" + $screenshotPath
        & $edge `
            --headless=new `
            --disable-gpu `
            --no-first-run `
            --virtual-time-budget=7000 `
            --window-size=1440,1200 `
            $screenshotProfileArg `
            $screenshotArg `
            $frontendUrl | Out-Null
        $screenshotDeadline = (Get-Date).AddSeconds(5)
        while (-not (Test-Path $screenshotPath) -and (Get-Date) -lt $screenshotDeadline) {
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path $screenshotPath)) {
            throw "Headless browser screenshot failed."
        }
    } else {
        Write-Warning "Microsoft Edge was not found; API workflow passed but browser rendering was skipped."
    }

    Write-Host "System E2E passed."
    Write-Host "Report: $reportPath"
    if (Test-Path $screenshotPath) { Write-Host "Screenshot: $screenshotPath" }
}
finally {
    if ($createdIds.Count -eq 0 -and (Test-Path $reportPath)) {
        try {
            $createdIds = @((Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json).created_document_ids)
        }
        catch {
            Write-Warning "Could not read created document IDs from the E2E report."
        }
    }
    if (-not $KeepDocuments -and $createdIds.Count -gt 0) {
        for ($index = $createdIds.Count - 1; $index -ge 0; $index--) {
            $documentId = $createdIds[$index]
            try {
                Invoke-RestMethod `
                    -Method Delete `
                    -Uri "http://localhost:8000/api/v1/documents/$documentId" | Out-Null
            }
            catch {
                Write-Warning "Could not remove E2E document $documentId"
            }
        }
    }
    if ($null -eq $previousFrontendHostPort) {
        Remove-Item Env:FRONTEND_HOST_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:FRONTEND_HOST_PORT = $previousFrontendHostPort
    }
    if ($null -eq $previousFrontendOrigin) {
        Remove-Item Env:FRONTEND_ORIGIN -ErrorAction SilentlyContinue
    }
    else {
        $env:FRONTEND_ORIGIN = $previousFrontendOrigin
    }
    Pop-Location
}
