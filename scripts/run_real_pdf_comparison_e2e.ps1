param(
    [string]$CorpusDir = "output/pdf/regression-corpus",
    [int]$TimeoutSeconds = 600,
    [switch]$Gpu,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$corpus = (Resolve-Path (Join-Path $projectRoot $CorpusDir)).Path
$reportDir = Join-Path $projectRoot "backend/tmp/stage63-real-pdf"
$reportName = "bge-deepseek-comparison-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()).json"
$reportPath = Join-Path $reportDir $reportName
$containerReport = "/app/$reportName"
$manifestPath = Join-Path $projectRoot "docs/pdf-regression-corpus.json"
$datasetPath = Join-Path $projectRoot "backend/evals/real_pdf_comparison_grounded.json"
$dataset = Get-Content -Raw -LiteralPath $datasetPath | ConvertFrom-Json
$filenames = @($dataset.cases | ForEach-Object { $_.documents } | Select-Object -Unique)
$composeArgs = @("compose", "-f", "docker-compose.yml")
if ($Gpu) { $composeArgs += @("-f", "docker-compose.bge-gpu.yml") }
$composeArgs += @(
    "--env-file", ".env", "--env-file", ".env.bge", "--profile", "local-bge"
)

New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
Push-Location $projectRoot
try {
    if ($Build) {
        & docker @composeArgs build backend worker
        if ($LASTEXITCODE -ne 0) { throw "Backend/Worker build failed." }
    }
    & docker @composeArgs up -d postgres redis qdrant backend worker bge-embedding bge-reranker
    if ($LASTEXITCODE -ne 0) { throw "Required services failed to start." }

    $healthDeadline = (Get-Date).AddSeconds([Math]::Min($TimeoutSeconds, 180))
    do {
        try { $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" }
        catch { $health = $null }
        if ($health.status -eq "ok") { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $healthDeadline)
    if ($health.status -ne "ok") { throw "Backend health check timed out." }

    & docker @composeArgs exec -T backend python scripts/check_bge_services.py `
        --embedding-url http://bge-embedding --reranker-url http://bge-reranker
    if ($LASTEXITCODE -ne 0) { throw "BGE smoke test failed." }

    $documentIds = @()
    foreach ($filename in $filenames) {
        $pdfPath = Join-Path $corpus $filename
        if (-not (Test-Path -LiteralPath $pdfPath)) { throw "Missing PDF: $pdfPath" }
        $upload = Invoke-RestMethod -Method Post `
            -Uri "http://localhost:8000/api/v1/documents" `
            -Form @{ file = Get-Item -LiteralPath $pdfPath }
        $documentId = [string]$upload.document.id
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        do {
            $document = Invoke-RestMethod `
                -Uri "http://localhost:8000/api/v1/documents/$documentId"
            if ($document.status -eq "ready") { break }
            if ($document.status -eq "failed") {
                throw "PDF parsing failed for $filename`: $($document.error_message)"
            }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline)
        if ($document.status -ne "ready") { throw "PDF parsing timed out: $filename" }
        $documentIds += $documentId
        Write-Host "Ready: $filename ($documentId)"
    }

    $reindexArgs = @("scripts/reindex_vectors.py")
    foreach ($documentId in $documentIds) {
        $reindexArgs += @("--document-id", $documentId)
    }
    $reindexArgs += @("--strict")
    & docker @composeArgs exec -T backend python @reindexArgs
    if ($LASTEXITCODE -ne 0) { throw "BGE vector reindex failed." }

    $backendContainer = (& docker @composeArgs ps -q backend).Trim()
    if (-not $backendContainer) { throw "Backend container not found." }
    & docker cp $manifestPath "${backendContainer}:/app/stage63-pdf-manifest.json"
    if ($LASTEXITCODE -ne 0) { throw "Could not provide pinned PDF manifest." }

    & docker @composeArgs exec -T backend python `
        scripts/evaluate_real_pdf_comparison.py `
        --manifest /app/stage63-pdf-manifest.json `
        --output $containerReport `
        --strict
    $evaluationExitCode = $LASTEXITCODE
    & docker cp "${backendContainer}:$containerReport" $reportPath
    if ($LASTEXITCODE -ne 0) { throw "Could not copy evaluation report." }
    if ($evaluationExitCode -ne 0) { throw "Strict comparison evaluation failed: $reportPath" }
    Write-Host "Real-PDF BGE + DeepSeek evaluation passed. Report: $reportPath"
}
finally {
    Pop-Location
}
