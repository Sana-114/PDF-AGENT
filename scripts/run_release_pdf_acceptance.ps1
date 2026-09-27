param(
    [string]$CorpusDir = "output/pdf/regression-corpus",
    [int]$LongDocumentTimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$corpus = (Resolve-Path (Join-Path $projectRoot $CorpusDir)).Path
$manifest = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "docs/pdf-regression-corpus.json") |
    ConvertFrom-Json
$reportDir = Join-Path $projectRoot "backend/tmp/stage65-release"
$containerDir = "/app/tmp/stage65-release"
$filenames = @(
    "1706.03762v7.pdf",
    "1706.03762v1.pdf",
    "1706.03762v1_img.pdf",
    "UnderstandingDeepLearning_02_09_26_C.pdf"
)
$hashes = [ordered]@{}

New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
foreach ($filename in $filenames) {
    $path = Join-Path $corpus $filename
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing PDF: $path" }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $entry = @($manifest.downloads | Where-Object { $_.filename -eq $filename }) |
        Select-Object -First 1
    if ($entry -and $entry.sha256 -and $hash -ne $entry.sha256) {
        throw "Pinned SHA-256 mismatch for $filename"
    }
    $hashes[$filename] = $hash
}

Push-Location $projectRoot
try {
    $container = (& docker compose -f docker-compose.yml --env-file .env ps -q backend).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $container) {
        throw "Start the backend container before running PDF acceptance."
    }
    & docker exec $container mkdir -p "$containerDir/scan"
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare container test directory." }
    foreach ($filename in $filenames) {
        $destination = if ($filename -eq "1706.03762v1_img.pdf") {
            "$containerDir/scan/$filename"
        } else {
            "$containerDir/$filename"
        }
        & docker cp (Join-Path $corpus $filename) "${container}:$destination"
        if ($LASTEXITCODE -ne 0) { throw "Could not copy $filename into test container." }
    }
    & docker cp (Join-Path $projectRoot "docs/pdf-regression-corpus.json") `
        "${container}:$containerDir/manifest.json"
    if ($LASTEXITCODE -ne 0) { throw "Could not copy PDF expectations into test container." }

    $checks = @(
        [ordered]@{
            name = "v7_then_v1"
            report = "version-pair.json"
            args = @(
                "scripts/evaluate_version_pair.py",
                "$containerDir/1706.03762v7.pdf",
                "$containerDir/1706.03762v1.pdf",
                "--output", "$containerDir/version-pair.json", "--strict"
            )
        },
        [ordered]@{
            name = "scan_surrogate_ocr"
            report = "scan-ocr.json"
            args = @(
                "scripts/evaluate_pdf_corpus.py", "$containerDir/scan",
                "--manifest", "$containerDir/manifest.json",
                "--output", "$containerDir/scan-ocr.json", "--strict"
            )
        },
        [ordered]@{
            name = "real_541_page_book"
            report = "book-541.json"
            args = @(
                "scripts/evaluate_long_document.py",
                "$containerDir/UnderstandingDeepLearning_02_09_26_C.pdf",
                "--expected-pages", "541",
                "--max-elapsed-seconds", "$LongDocumentTimeoutSeconds",
                "--max-python-peak-memory-mb", "512",
                "--output", "$containerDir/book-541.json", "--strict"
            )
        }
    )
    $results = @()
    foreach ($check in $checks) {
        Write-Host "Running $($check.name)..."
        $commandArgs = [string[]]$check.args
        & docker exec $container python @commandArgs | Out-Null
        $processExit = $LASTEXITCODE
        $localReport = Join-Path $reportDir $check.report
        & docker cp "${container}:$containerDir/$($check.report)" $localReport
        if ($LASTEXITCODE -ne 0) { throw "Could not copy $($check.name) report." }
        $result = Get-Content -Raw -LiteralPath $localReport | ConvertFrom-Json
        $status = if ($check.name -eq "scan_surrogate_ocr") {
            if ($result.summary.failed -eq 0 -and $result.summary.total -eq 1) {
                "passed"
            } else { "failed" }
        } else { $result.status }
        if ($processExit -ne 0) { $status = "failed" }
        $results += [ordered]@{
            name = $check.name
            status = $status
            report = $localReport
        }
        Write-Host "$($check.name): $status"
    }
    $summary = [ordered]@{
        status = if (@($results | Where-Object { $_.status -ne "passed" }).Count) {
            "failed"
        } else { "passed" }
        official_scan_tested = $false
        scan_note = "1706.03762v1_img.pdf is a locally rasterized surrogate, not the organizer's file."
        source_sha256 = $hashes
        checks = $results
    }
    $summaryPath = Join-Path $reportDir "pdf-acceptance-summary.json"
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding utf8
    if ($summary.status -ne "passed") { throw "PDF acceptance failed: $summaryPath" }
    Write-Host "PDF acceptance passed. Summary: $summaryPath"
}
finally {
    Pop-Location
}
