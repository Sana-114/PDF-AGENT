param(
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8000,
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

if ($RequireBge) {
    foreach ($service in @("bge-embedding", "bge-reranker")) {
        if ($runningServices -notcontains $service) {
            $failures.Add("Required BGE service '$service' is not running.")
        }
    }
    Test-HttpEndpoint -Name "BGE embedding" -Uri "http://localhost:8001/info"
    Test-HttpEndpoint -Name "BGE reranker" -Uri "http://localhost:8002/info"
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
