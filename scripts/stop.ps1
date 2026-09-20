[CmdletBinding()]
param(
    [switch]$RemoveContainers
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    if ($RemoveContainers) {
        docker compose --profile local-bge down
    }
    else {
        docker compose --profile local-bge stop
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to stop the PaperPilot services."
    }
    Write-Host "PaperPilot services stopped. Named volumes and model caches were preserved."
}
finally {
    Pop-Location
}
