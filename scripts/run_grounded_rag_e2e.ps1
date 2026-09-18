param(
    [string]$PdfPath = "output/pdf/regression-corpus/1706.03762v1.pdf",
    [ValidateSet("lexical", "configured")]
    [string]$Retriever = "lexical",
    [int]$TimeoutSeconds = 300,
    [switch]$LocalBge,
    [switch]$Gpu,
    [switch]$CleanupImported
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = (Resolve-Path (Join-Path $projectRoot $PdfPath)).Path
$reportDir = Join-Path $projectRoot "backend/tmp/grounded-rag"
$reportMode = if ($LocalBge) { "bge-$Retriever" } else { $Retriever }
$retrievalReport = Join-Path $reportDir "attention-v1-$reportMode-retrieval.json"
$groundedReport = Join-Path $reportDir "attention-v1-$reportMode-deepseek.json"
$createdDocument = $false
$documentId = $null
$composeArgs = @("compose")

if ($Gpu -and -not $LocalBge) {
    throw "-Gpu requires -LocalBge."
}
if ($LocalBge -and $Retriever -ne "configured") {
    throw "-LocalBge requires -Retriever configured."
}
if ($LocalBge) {
    if ($Gpu) {
        $composeArgs += @(
            "-f", "docker-compose.yml",
            "-f", "docker-compose.bge-gpu.yml"
        )
    }
    $composeArgs += @(
        "--env-file", ".env",
        "--env-file", ".env.bge",
        "--profile", "local-bge"
    )
}

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

Push-Location $projectRoot
try {
    $services = @("postgres", "redis", "qdrant", "backend", "worker")
    if ($LocalBge) { $services += @("bge-embedding", "bge-reranker") }
    & docker @composeArgs up -d @services
    if ($LASTEXITCODE -ne 0) { throw "Docker services failed to start." }

    $healthDeadline = (Get-Date).AddSeconds([Math]::Min($TimeoutSeconds, 120))
    do {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health"
        }
        catch {
            $health = $null
        }
        if ($health.status -eq "ok") { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $healthDeadline)
    if ($health.status -ne "ok") { throw "Backend health check timed out." }

    $upload = Invoke-RestMethod `
        -Method Post `
        -Uri "http://localhost:8000/api/v1/documents" `
        -Form @{ file = Get-Item -LiteralPath $source }
    $documentId = [string]$upload.document.id
    $createdDocument = -not [bool]$upload.exact_duplicate

    $parseDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $document = Invoke-RestMethod `
            -Uri "http://localhost:8000/api/v1/documents/$documentId"
        if ($document.status -eq "ready") { break }
        if ($document.status -eq "failed") {
            throw "PDF parsing failed: $($document.error_message)"
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $parseDeadline)
    if ($document.status -ne "ready") { throw "PDF parsing timed out." }

    if ($LocalBge) {
        & docker @composeArgs exec -T backend python scripts/check_bge_services.py `
            --embedding-url http://bge-embedding `
            --reranker-url http://bge-reranker
        if ($LASTEXITCODE -ne 0) { throw "BGE service smoke test failed." }

        & docker @composeArgs exec -T backend python scripts/reindex_vectors.py `
            --document-id $documentId `
            --output "/app/tmp/grounded-rag/attention-v1-bge-reindex.json" `
            --strict
        if ($LASTEXITCODE -ne 0) { throw "BGE vector reindex failed." }
    }

    $retrievalArgs = @(
        "scripts/evaluate_retrieval.py",
        "evals/attention_v1.json",
        "--retriever", $Retriever,
        "--output", "/app/tmp/grounded-rag/attention-v1-$reportMode-retrieval.json",
        "--summary-only",
        "--strict"
    )
    if ($LocalBge) { $retrievalArgs += @("--require-retrieval-mode", "reranked") }
    & docker @composeArgs exec -T backend python @retrievalArgs
    if ($LASTEXITCODE -ne 0) { throw "Retrieval evaluation failed." }

    $groundedArgs = @(
        "scripts/evaluate_grounded_rag.py",
        "evals/attention_v1_grounded.json",
        "--retriever", $Retriever,
        "--output", "/app/tmp/grounded-rag/attention-v1-$reportMode-deepseek.json",
        "--summary-only",
        "--strict"
    )
    if ($LocalBge) { $groundedArgs += @("--require-retrieval-mode", "reranked") }
    & docker @composeArgs exec -T backend python @groundedArgs
    if ($LASTEXITCODE -ne 0) { throw "Grounded RAG evaluation failed." }

    Write-Host "Grounded RAG E2E passed for document $documentId."
    Write-Host "Retrieval report: $retrievalReport"
    Write-Host "DeepSeek report: $groundedReport"
}
finally {
    if ($CleanupImported -and $createdDocument -and $documentId) {
        try {
            Invoke-RestMethod `
                -Method Delete `
                -Uri "http://localhost:8000/api/v1/documents/$documentId" | Out-Null
        }
        catch {
            Write-Warning "Could not remove imported document $documentId"
        }
    }
    Pop-Location
}
