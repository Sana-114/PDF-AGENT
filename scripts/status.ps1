[CmdletBinding()]
param(
    [ValidateSet("any", "lite", "standard")]
    [string]$Mode = "any"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Get-EnvValue {
    param([string]$Path, [string]$Name, [string]$Default)
    if (-not (Test-Path -LiteralPath $Path)) { return $Default }
    $line = Get-Content -LiteralPath $Path | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -Last 1
    if (-not $line) { return $Default }
    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

Push-Location $repoRoot
try {
    $envPath = Join-Path $repoRoot ".env"
    $bgeEnvPath = Join-Path $repoRoot ".env.bge"
    $frontendPort = [int](Get-EnvValue -Path $envPath -Name "FRONTEND_HOST_PORT" -Default "3000")
    $embeddingPort = [int](Get-EnvValue -Path $bgeEnvPath -Name "BGE_EMBEDDING_PORT" -Default "8001")
    $rerankerPort = [int](Get-EnvValue -Path $bgeEnvPath -Name "BGE_RERANKER_PORT" -Default "8002")

    docker compose --profile local-bge ps
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker Compose services." }

    & (Join-Path $PSScriptRoot "check_runtime.ps1") `
        -FrontendPort $frontendPort `
        -BgeEmbeddingPort $embeddingPort `
        -BgeRerankerPort $rerankerPort `
        -Mode $Mode
}
finally {
    Pop-Location
}
