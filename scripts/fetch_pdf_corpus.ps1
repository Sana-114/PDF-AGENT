param(
    [string]$OutputDir = "output/pdf/regression-corpus",
    [string]$Proxy = "",
    [string[]]$PaperIds = @()
)

$ErrorActionPreference = "Stop"
$papers = @(
    @{ Id = "transformer-v1"; Filename = "1706.03762v1.pdf"; Url = "https://arxiv.org/pdf/1706.03762v1.pdf" },
    @{ Id = "transformer-v7"; Filename = "1706.03762v7.pdf"; Url = "https://arxiv.org/pdf/1706.03762v7.pdf" },
    @{ Id = "bert-v2"; Filename = "1810.04805v2.pdf"; Url = "https://arxiv.org/pdf/1810.04805v2.pdf" },
    @{ Id = "rag-v4"; Filename = "2005.11401v4.pdf"; Url = "https://arxiv.org/pdf/2005.11401v4.pdf" },
    @{
        Id = "understanding-deep-learning-2026"
        Filename = "UnderstandingDeepLearning_02_09_26_C.pdf"
        Url = "https://github.com/udlbook/udlbook/releases/download/v5.0.3/UnderstandingDeepLearning_02_09_26_C.pdf"
    }
)

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
        Write-Host "Already present: $target"
        continue
    }
    $parameters = @{
        Uri = $paper.Url
        OutFile = $target
        UserAgent = "PaperPilot regression corpus/1.0"
    }
    if ($Proxy) {
        $parameters.Proxy = $Proxy
    }
    Write-Host "Downloading $($paper.Url)"
    Invoke-WebRequest @parameters
    $header = [System.IO.File]::ReadAllBytes($target)[0..4]
    if ([System.Text.Encoding]::ASCII.GetString($header) -ne "%PDF-") {
        Remove-Item -LiteralPath $target
        throw "Downloaded file is not a PDF: $target"
    }
}
