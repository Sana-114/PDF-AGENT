param(
    [string]$CorpusDir = "output/pdf/regression-corpus",
    [int]$LongDocumentTimeoutSeconds = 900,
    [string]$ManifestPath = "docs/pdf-regression-corpus.json",
    [string]$ReportDir = "backend/tmp/stage65-release",
    [switch]$OfficialScan
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$corpus = (Resolve-Path (Join-Path $projectRoot $CorpusDir)).Path
$manifestFile = (Resolve-Path (Join-Path $projectRoot $ManifestPath)).Path
$manifest = Get-Content -Raw -LiteralPath $manifestFile |
    ConvertFrom-Json
$reportDir = Join-Path $projectRoot $ReportDir
if ($OfficialScan -and $manifest.origin -ne "user-provided-organizer-corpus") {
    throw "Official scan requires the pinned organizer corpus manifest."
}
if (Test-Path -LiteralPath (Join-Path $reportDir "pdf-acceptance-summary.json")) {
    throw "Report already exists; use a new -ReportDir to preserve earlier results."
}
$containerDir = "/app/tmp/pdf-acceptance-$([guid]::NewGuid().ToString('N'))"
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
    & docker exec $container mkdir -p "$containerDir/scan" "$containerDir/native" "$containerDir/scripts"
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
    foreach ($filename in @("1706.03762v1.pdf", "1706.03762v7.pdf")) {
        & docker cp (Join-Path $corpus $filename) "${container}:$containerDir/native/$filename"
        if ($LASTEXITCODE -ne 0) { throw "Could not copy native PDF: $filename" }
    }
    & docker cp $manifestFile `
        "${container}:$containerDir/manifest.json"
    if ($LASTEXITCODE -ne 0) { throw "Could not copy PDF expectations into test container." }
    $scriptHashes = [ordered]@{}
    foreach ($script in @("evaluate_pdf_corpus.py", "evaluate_version_pair.py", "evaluate_long_document.py")) {
        $scriptPath = Join-Path $projectRoot "backend/scripts/$script"
        & docker cp $scriptPath "${container}:$containerDir/scripts/$script"
        if ($LASTEXITCODE -ne 0) { throw "Could not stage acceptance script: $script" }
        $scriptHashes[$script] = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $checks = @(
        [ordered]@{
            name = "native_structure"
            report = "native-structure.json"
            args = @(
                "scripts/evaluate_pdf_corpus.py", "$containerDir/native",
                "--manifest", "$containerDir/manifest.json",
                "--output", "$containerDir/native-structure.json", "--strict"
            )
        },
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
            name = if ($OfficialScan) { "official_scan_ocr" } else { "scan_surrogate_ocr" }
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
        $commandArgs[0] = "$containerDir/$($commandArgs[0])"
        & docker exec -e PYTHONPATH=/app $container python @commandArgs | Out-Null
        $processExit = $LASTEXITCODE
        $localReport = Join-Path $reportDir $check.report
        & docker cp "${container}:$containerDir/$($check.report)" $localReport
        if ($LASTEXITCODE -ne 0) { throw "Could not copy $($check.name) report." }
        $result = Get-Content -Raw -LiteralPath $localReport | ConvertFrom-Json
        $status = if ($check.name -in @("scan_surrogate_ocr", "official_scan_ocr", "native_structure")) {
            $expectedTotal = if ($check.name -eq "native_structure") { 2 } else { 1 }
            if ($result.summary.failed -eq 0 -and $result.summary.total -eq $expectedTotal) {
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
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
        code_revision = (& git -c "safe.directory=$projectRoot" rev-parse HEAD).Trim()
        code_worktree_dirty = [bool](& git -c "safe.directory=$projectRoot" status --porcelain)
        acceptance_script_sha256 = $scriptHashes
        backend_container = $container
        backend_image = (& docker inspect --format '{{.Image}}' $container).Trim()
        manifest_sha256 = (Get-FileHash -LiteralPath $manifestFile -Algorithm SHA256).Hash.ToLowerInvariant()
        external_llm_called = $false
        production_library_modified = $false
        official_scan_tested = [bool]$OfficialScan
        scan_note = if ($OfficialScan) { "User-provided organizer file, verified against the official manifest." } else {
            "1706.03762v1_img.pdf is a locally rasterized surrogate, not the organizer's file."
        }
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
