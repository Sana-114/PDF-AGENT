param(
    [string]$OutputDir = "output/pdf/regression-corpus",
    [string]$ManifestPath = "docs/pdf-regression-corpus.json",
    [string]$Proxy = "",
    [string[]]$PaperIds = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-ValidatedPdfSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ExpectedSha256 = ""
    )

    $header = [byte[]]::new(5)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Read($header, 0, 5) -ne 5 -or [System.Text.Encoding]::ASCII.GetString($header) -ne "%PDF-") {
            throw "File is not a PDF: $Path"
        }
    }
    finally {
        $stream.Dispose()
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ExpectedSha256 -and $actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "SHA-256 mismatch for ${Path}: expected $ExpectedSha256, received $actual"
    }
    return $actual
}

if (-not (Test-Path -LiteralPath $ManifestPath)) {
    throw "Corpus manifest does not exist: $ManifestPath"
}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$papers = @($manifest.downloads | ForEach-Object {
    if (-not $_.id -or -not $_.filename -or -not $_.source) {
        throw "Each manifest download requires id, filename, and source."
    }
    $sourceUri = [uri]$_.source
    if ($sourceUri.Scheme -ne "https") {
        throw "Corpus source must use HTTPS: $($_.source)"
    }
    $expectedSha256 = if ($_.PSObject.Properties["sha256"]) { [string]$_.sha256 } else { "" }
    if ($expectedSha256 -and $expectedSha256 -notmatch "^[a-fA-F0-9]{64}$") {
        throw "Corpus SHA-256 must contain exactly 64 hexadecimal characters: $($_.id)"
    }
    [pscustomobject]@{
        Id = [string]$_.id
        Filename = [string]$_.filename
        Url = $sourceUri.AbsoluteUri
        ExpectedSha256 = $expectedSha256.ToLowerInvariant()
    }
})

if ($PaperIds.Count -gt 0) {
    $knownIds = @($papers | ForEach-Object { $_.Id })
    $unknownIds = @($PaperIds | Where-Object { $_ -notin $knownIds })
    if ($unknownIds.Count -gt 0) {
        throw "Unknown paper id(s): $($unknownIds -join ', '). Available ids: $($knownIds -join ', ')"
    }
    $papers = @($papers | Where-Object { $_.Id -in $PaperIds })
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
foreach ($paper in $papers) {
    $target = Join-Path $OutputDir $paper.Filename
    if (Test-Path -LiteralPath $target) {
        $hash = Get-ValidatedPdfSha256 -Path $target -ExpectedSha256 $paper.ExpectedSha256
        Write-Host "Already present: $target (sha256=$hash)"
        continue
    }
    $partial = "$target.partial"
    $parameters = @{
        Uri = $paper.Url
        OutFile = $partial
        UserAgent = "PaperPilot regression corpus/1.0"
    }
    if ($Proxy) {
        $parameters.Proxy = $Proxy
    }
    Write-Host "Downloading $($paper.Url)"
    try {
        Invoke-WebRequest @parameters
        $hash = Get-ValidatedPdfSha256 -Path $partial -ExpectedSha256 $paper.ExpectedSha256
        Move-Item -LiteralPath $partial -Destination $target
    }
    finally {
        if (Test-Path -LiteralPath $partial) {
            Remove-Item -LiteralPath $partial
        }
    }
    $sizeMb = [math]::Round((Get-Item -LiteralPath $target).Length / 1MB, 2)
    Write-Host "Saved: $target ($sizeMb MiB, sha256=$hash)"
}
