param(
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8000,
    [int]$BgeEmbeddingPort = 8001,
    [int]$BgeRerankerPort = 8002,
    [ValidateSet("any", "lite", "standard")]
    [string]$Mode = "any",
    [switch]$RequireBge
)

$ErrorActionPreference = "Stop"
$failures = [System.Collections.Generic.List[string]]::new()

function Test-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Uri,
        [scriptblock]$Validate
    )

    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -ne 200) {
            $script:failures.Add("$Name returned HTTP $($response.StatusCode).")
            return
        }
        if ($Validate -and -not (& $Validate $response)) {
            $script:failures.Add("$Name returned an unexpected response.")
            return
        }
        Write-Host "[ok] $Name -> $Uri"
    }
    catch {
        $script:failures.Add("$Name is unavailable: $($_.Exception.Message)")
    }
}

docker version --format '{{.Server.Version}}' *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable. Start Docker Desktop and retry."
}

$runningServices = @(docker compose --profile local-bge ps --status running --services)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the Docker Compose project."
}

$requiredServices = @("postgres", "redis", "qdrant", "minio", "backend", "worker", "beat", "frontend")
foreach ($service in $requiredServices) {
    if ($runningServices -notcontains $service) {
        $failures.Add("Compose service '$service' is not running.")
    }
    else {
        Write-Host "[ok] service $service"
    }
}

Test-HttpEndpoint -Name "Backend health" -Uri "http://localhost:$BackendPort/api/v1/health" -Validate {
    param($response)
    ($response.Content | ConvertFrom-Json).status -eq "ok"
}
Test-HttpEndpoint -Name "Frontend" -Uri "http://localhost:$FrontendPort/" -Validate {
    param($response)
    $response.Content -match "PaperPilot"
}

if ($runningServices -contains "worker") {
    $workerPing = docker compose exec -T worker celery -A app.workers.celery_app:celery_app inspect ping --timeout=5 2>&1
    if ($LASTEXITCODE -ne 0 -or ($workerPing -join "`n") -notmatch "pong") {
        $failures.Add("Celery Worker did not answer inspect ping.")
    }
    else {
        Write-Host "[ok] Celery Worker ping"
    }
}

$providerJson = docker compose exec -T backend python -c "import json; from app.core.config import settings; print(json.dumps({'embedding': settings.embedding_provider, 'reranker': settings.reranker_provider}))" 2>&1
if ($LASTEXITCODE -ne 0) {
    $failures.Add("Unable to read the active retrieval providers from Backend.")
}
else {
    try {
        $providers = ($providerJson | Select-Object -Last 1) | ConvertFrom-Json
        Write-Host "[ok] retrieval providers: embedding=$($providers.embedding), reranker=$($providers.reranker)"
        if ($Mode -eq "lite" -and ($providers.embedding -ne "hash" -or $providers.reranker -ne "none")) {
            $failures.Add("Lite mode expected embedding=hash and reranker=none.")
        }
        if ($Mode -eq "standard" -and ($providers.embedding -ne "openai-compatible" -or $providers.reranker -ne "tei")) {
            $failures.Add("Standard mode expected embedding=openai-compatible and reranker=tei.")
        }
    }
    catch {
        $failures.Add("Backend returned invalid retrieval provider metadata.")
    }
}

if ($RequireBge -or $Mode -eq "standard") {
    foreach ($service in @("bge-embedding", "bge-reranker")) {
        if ($runningServices -notcontains $service) {
            $failures.Add("Required BGE service '$service' is not running.")
        }
    }
    Test-HttpEndpoint -Name "BGE embedding" -Uri "http://localhost:$BgeEmbeddingPort/info"
    Test-HttpEndpoint -Name "BGE reranker" -Uri "http://localhost:$BgeRerankerPort/info"
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "Runtime check failed:" -ForegroundColor Red
    foreach ($failure in $failures) {
        Write-Host "- $failure" -ForegroundColor Red
    }
    exit 1
}

Write-Host ""
Write-Host "PaperPilot runtime is healthy." -ForegroundColor Green
