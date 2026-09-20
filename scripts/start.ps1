[CmdletBinding()]
param(
    [ValidateSet("lite", "standard")]
    [string]$Mode = "lite",
    [switch]$Gpu,
    [switch]$NoBuild,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$bgeEnvPath = Join-Path $repoRoot ".env.bge"
$samePowerShell = Join-Path $PSHOME "pwsh.exe"
if (-not (Test-Path -LiteralPath $samePowerShell)) {
    $samePowerShell = "powershell.exe"
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Default = ""
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $Default
    }
    $line = Get-Content -LiteralPath $Path | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -Last 1
    if (-not $line) {
        return $Default
    }
    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($Arguments -join ' ')"
    }
}

function Test-PortFree {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $envPath)) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".env.example") -Destination $envPath
        Write-Host "Created .env from .env.example"
    }
    if ($Mode -eq "standard" -and -not (Test-Path -LiteralPath $bgeEnvPath)) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".env.bge.example") -Destination $bgeEnvPath
        Write-Host "Created .env.bge from .env.bge.example"
    }

    docker version --format '{{.Server.Version}}' *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engine is unavailable. Start Docker Desktop and retry."
    }

    $frontendPort = [int](Get-EnvValue -Path $envPath -Name "FRONTEND_HOST_PORT" -Default "3000")
    $backendPort = 8000
    $redisPort = [int](Get-EnvValue -Path $envPath -Name "REDIS_HOST_PORT" -Default "6379")
    $qdrantHttpPort = [int](Get-EnvValue -Path $envPath -Name "QDRANT_HTTP_PORT" -Default "6333")
    $qdrantGrpcPort = [int](Get-EnvValue -Path $envPath -Name "QDRANT_GRPC_PORT" -Default "6334")
    $embeddingPort = [int](Get-EnvValue -Path $bgeEnvPath -Name "BGE_EMBEDDING_PORT" -Default "8001")
    $rerankerPort = [int](Get-EnvValue -Path $bgeEnvPath -Name "BGE_RERANKER_PORT" -Default "8002")

    if ($Gpu -and $Mode -ne "standard") {
        throw "-Gpu is only valid with -Mode standard."
    }

    $runningServices = @(docker compose --profile local-bge ps --status running --services 2>$null)
    if ($runningServices.Count -eq 0) {
        $ports = @(
            $frontendPort, $backendPort, $redisPort,
            $qdrantHttpPort, $qdrantGrpcPort,
            5432, 9000, 9001
        )
        if ($Mode -eq "standard") {
            $ports += @($embeddingPort, $rerankerPort)
        }
        $occupiedPorts = @($ports | Sort-Object -Unique | Where-Object { -not (Test-PortFree -Port $_) })
        if ($occupiedPorts.Count -gt 0) {
            throw "Required host ports are already occupied: $($occupiedPorts -join ', ')."
        }
    }

    $memoryBytes = [double](docker info --format '{{.MemTotal}}')
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Docker memory capacity."
    }
    $memoryGiB = [math]::Round($memoryBytes / 1GB, 1)
    Write-Host "Docker memory: $memoryGiB GiB"
    if ($Mode -eq "standard" -and $memoryGiB -lt 20) {
        Write-Warning "Standard mode is memory intensive. 20 GiB or more is recommended; current limit is $memoryGiB GiB."
    }

    $llmProvider = Get-EnvValue -Path $envPath -Name "LLM_PROVIDER" -Default "mock"
    $llmKey = Get-EnvValue -Path $envPath -Name "LLM_API_KEY"
    if ($Mode -eq "standard" -and $llmProvider -ne "deepseek") {
        Write-Warning "Standard retrieval will start, but LLM_PROVIDER is '$llmProvider' instead of 'deepseek'."
    }
    if ($llmProvider -eq "deepseek" -and [string]::IsNullOrWhiteSpace($llmKey)) {
        Write-Warning "LLM_PROVIDER=deepseek but LLM_API_KEY is empty. Generated answers will fail until a key is configured."
    }

    if ($Mode -eq "standard") {
        $imageVariable = if ($Gpu) { "TEI_GPU_IMAGE" } else { "TEI_IMAGE" }
        $defaultImage = if ($Gpu) {
            "ghcr.io/huggingface/text-embeddings-inference:89-1.9"
        }
        else {
            "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9"
        }
        $selectedImage = Get-EnvValue -Path $bgeEnvPath -Name $imageVariable -Default $defaultImage
        docker image inspect $selectedImage *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "The selected TEI image '$selectedImage' is not cached and Docker will download it during startup."
        }
    }

    if ($Mode -eq "lite") {
        Invoke-Docker -Arguments @("compose", "--profile", "local-bge", "stop", "bge-embedding", "bge-reranker")
        $composeArguments = @(
            "compose", "--env-file", ".env",
            "-f", "docker-compose.yml",
            "-f", "docker-compose.lite.yml"
        )
    }
    else {
        $composeArguments = @(
            "compose", "--env-file", ".env", "--env-file", ".env.bge",
            "--profile", "local-bge",
            "-f", "docker-compose.yml"
        )
        if ($Gpu) {
            $composeArguments += @("-f", "docker-compose.bge-gpu.yml")
        }
    }

    $upArguments = $composeArguments + @("up", "-d")
    if (-not $NoBuild) {
        $upArguments += "--build"
    }
    Invoke-Docker -Arguments $upArguments

    $checkScript = Join-Path $PSScriptRoot "check_runtime.ps1"
    $checkArguments = @(
        "-NoProfile", "-File", $checkScript,
        "-FrontendPort", "$frontendPort",
        "-BackendPort", "$backendPort",
        "-BgeEmbeddingPort", "$embeddingPort",
        "-BgeRerankerPort", "$rerankerPort",
        "-Mode", $Mode
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        & $samePowerShell @checkArguments *> $null
        if ($LASTEXITCODE -eq 0) {
            break
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    & $samePowerShell @checkArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PaperPilot did not become healthy within $TimeoutSeconds seconds."
    }

    Write-Host ""
    Write-Host "PaperPilot '$Mode' mode is ready: http://localhost:$frontendPort/" -ForegroundColor Green
}
finally {
    Pop-Location
}
