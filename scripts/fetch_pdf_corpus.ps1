param(
    [string]$OutputDir = "output/pdf/regression-corpus",
    [string]$Proxy = ""
)

$ErrorActionPreference = "Stop"
$papers = @(
    @{ Filename = "1706.03762v1.pdf"; Url = "https://arxiv.org/pdf/1706.03762v1.pdf" },
    @{ Filename = "1706.03762v7.pdf"; Url = "https://arxiv.org/pdf/1706.03762v7.pdf" },
    @{ Filename = "1810.04805v2.pdf"; Url = "https://arxiv.org/pdf/1810.04805v2.pdf" },
    @{ Filename = "2005.11401v4.pdf"; Url = "https://arxiv.org/pdf/2005.11401v4.pdf" },
    @{
        Filename = "UnderstandingDeepLearning_02_09_26_C.pdf"
        Url = "https://github.com/udlbook/udlbook/releases/download/v5.0.3/UnderstandingDeepLearning_02_09_26_C.pdf"
    }
)

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
