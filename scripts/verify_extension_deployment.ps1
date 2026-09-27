[CmdletBinding()]
param(
    [string]$Destination = "C:\PASI\pasi-chatgpt-unpacked",
    [string]$BridgeUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"

$manifestPath = Join-Path $Destination "manifest.json"
$metadataPath = Join-Path $Destination ".pasi-deployment.json"

if (-not (Test-Path $manifestPath)) {
    throw "No deployed manifest found at $manifestPath"
}
if (-not (Test-Path $metadataPath)) {
    throw "No deployment metadata found at $metadataPath"
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$metadata = Get-Content $metadataPath -Raw | ConvertFrom-Json

Write-Host "PASI deployed extension:"
Write-Host "  name    : $($manifest.name)"
Write-Host "  version : $($manifest.version)"
Write-Host "  commit  : $($metadata.source_commit)"
Write-Host "  path    : $Destination"
Write-Host "  bridge  : $($metadata.bridge_url)"

$bridgeUri = [Uri]$BridgeUrl
$healthUri = [Uri]::new($bridgeUri, "/health")

try {
    $health = Invoke-RestMethod -Uri $healthUri.AbsoluteUri -Method Get -TimeoutSec 5
    Write-Host "Bridge health: reachable"
    $health | ConvertTo-Json -Depth 6
} catch {
    Write-Error "Bridge health check failed at $($healthUri.AbsoluteUri): $($_.Exception.Message)"
    exit 2
}

Write-Host "Deployment verification complete."
