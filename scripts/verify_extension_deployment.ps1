[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Destination = "C:\PASI\pasi-chatgpt-unpacked",
    [string]$BridgeUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$manifestPath = Join-Path $Destination "manifest.json"
$metadataPath = Join-Path $Destination ".pasi-deployment.json"
$runtimeTokenPath = Join-Path $RepoRoot ".runtime\bridge-token"
$sourceTokenPath = Join-Path $RepoRoot "extensions\pasi-chatgpt\.bridge-token"
$deployedTokenPath = Join-Path $Destination ".bridge-token"

foreach ($path in @($manifestPath, $metadataPath, $runtimeTokenPath, $sourceTokenPath, $deployedTokenPath)) {
    if (-not (Test-Path $path)) {
        throw "Required deployment artifact is missing: $path"
    }
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$metadata = Get-Content $metadataPath -Raw | ConvertFrom-Json
$sourceToken = (Get-Content $sourceTokenPath -Raw).Trim()
$runtimeToken = (Get-Content $runtimeTokenPath -Raw).Trim()
$deployedToken = (Get-Content $deployedTokenPath -Raw).Trim()
$currentSha = (& git -C $RepoRoot rev-parse HEAD).Trim()

if ($metadata.source_commit -ne $currentSha) {
    throw "Deployed extension commit $($metadata.source_commit) does not match Engineering Workspace commit $currentSha"
}
if ([string]::IsNullOrWhiteSpace($sourceToken) -or $sourceToken -ne $runtimeToken -or $sourceToken -ne $deployedToken) {
    throw "Bridge token is not synchronized across runtime, source extension, and deployed extension"
}

$requiredScripts = @(
    "src\timeout-config.js",
    "src\detectors.js",
    "src\recovery_progress.js",
    "src\content.js",
    "src\recovery.js"
)
foreach ($script in $requiredScripts) {
    if (-not (Test-Path (Join-Path $Destination $script))) {
        throw "Deployed native controller asset is missing: $script"
    }
}

Write-Host "PASI deployed extension:"
Write-Host "  name    : $($manifest.name)"
Write-Host "  version : $($manifest.version)"
Write-Host "  commit  : $($metadata.source_commit)"
Write-Host "  path    : $Destination"
Write-Host "  bridge  : $BridgeUrl"
Write-Host "  token   : synchronized"

$headers = @{ Authorization = "Bearer $deployedToken" }
$bridgeUri = [Uri]$BridgeUrl
$healthUri = [Uri]::new($bridgeUri, "/health")

try {
    $health = Invoke-RestMethod -Uri $healthUri.AbsoluteUri -Method Get -Headers $headers -TimeoutSec 5
    if ($health.status -ne "ok") {
        throw "unexpected bridge health response"
    }
    Write-Host "Bridge health: reachable and authenticated"
} catch {
    Write-Error "Bridge health check failed at $($healthUri.AbsoluteUri): $($_.Exception.Message)"
    exit 2
}

Write-Host "Deployment verification PASSED."
