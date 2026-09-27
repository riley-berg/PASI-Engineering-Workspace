[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Destination = "C:\PASI\pasi-chatgpt-unpacked",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$buildScript = Join-Path $RepoRoot "scripts\build_extension.ps1"
$source = Join-Path $RepoRoot "extensions\pasi-chatgpt"

if (-not (Test-Path $buildScript)) {
    throw "Missing build script: $buildScript"
}
if (-not (Test-Path $source)) {
    throw "Missing source extension: $source"
}

& $buildScript -RepoRoot $RepoRoot -SkipInstall:$SkipInstall
if ($LASTEXITCODE -ne 0) {
    throw "Extension build failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null

& robocopy.exe $source $Destination /MIR /XJ /R:2 /W:1 "/XD" ".git" "/XF" ".DS_Store"
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -ge 8) {
    throw "robocopy failed with exit code $robocopyExit"
}

$gitSha = (& git -C $RepoRoot rev-parse HEAD).Trim()
$deployedMetadata = [ordered]@{
    source_commit = $gitSha
    source_extension = $source
    deployed_extension = $Destination
    bridge_url = "http://127.0.0.1:8765"
    deployed_at_utc = [DateTime]::UtcNow.ToString("o")
}

$deployedMetadataPath = Join-Path $Destination ".pasi-deployment.json"
$deployedMetadata | ConvertTo-Json -Depth 4 | Set-Content -Path $deployedMetadataPath -Encoding UTF8

Write-Host ""
Write-Host "PASI extension deployment complete."
Write-Host "  source commit : $gitSha"
Write-Host "  source        : $source"
Write-Host "  deployed to   : $Destination"
Write-Host "  bridge        : http://127.0.0.1:8765"
Write-Host ""
Write-Host "Opera GX: open opera://extensions, enable Developer mode, then Reload the PASI extension."
Write-Host "Do not load any second PASI unpacked-extension directory."
