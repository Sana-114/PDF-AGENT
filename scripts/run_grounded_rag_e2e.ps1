param(
    [string]$PdfPath = "output/pdf/regression-corpus/1706.03762v1.pdf",
    [ValidateSet("lexical", "configured")]
    [string]$Retriever = "lexical",
    [int]$TimeoutSeconds = 300,
    [switch]$CleanupImported
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = (Resolve-Path (Join-Path $projectRoot $PdfPath)).Path
$reportDir = Join-Path $projectRoot "backend/tmp/grounded-rag"
$retrievalReport = Join-Path $reportDir "attention-v1-$Retriever-retrieval.json"
$groundedReport = Join-Path $reportDir "attention-v1-$Retriever-deepseek.json"
$createdDocument = $false
$documentId = $null

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

Push-Location $projectRoot
try {
    docker compose up -d postgres redis qdrant backend worker
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

    docker compose exec -T backend python scripts/evaluate_retrieval.py `
        evals/attention_v1.json `
        --retriever $Retriever `
        --output "/app/tmp/grounded-rag/attention-v1-$Retriever-retrieval.json" `
        --summary-only `
        --strict
    if ($LASTEXITCODE -ne 0) { throw "Retrieval evaluation failed." }

    docker compose exec -T backend python scripts/evaluate_grounded_rag.py `
        evals/attention_v1_grounded.json `
        --retriever $Retriever `
        --output "/app/tmp/grounded-rag/attention-v1-$Retriever-deepseek.json" `
        --summary-only `
        --strict
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
