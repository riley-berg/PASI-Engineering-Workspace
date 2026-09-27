[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$extensionRoot = Join-Path $RepoRoot "extensions\pasi-chatgpt"
$manifestPath = Join-Path $extensionRoot "manifest.json"
$packageJsonPath = Join-Path $RepoRoot "package.json"

if (-not (Test-Path $manifestPath)) {
    throw "Missing extension manifest: $manifestPath"
}
if (-not (Test-Path $packageJsonPath)) {
    throw "Missing package.json: $packageJsonPath"
}

Push-Location $RepoRoot
try {
    if (-not $SkipInstall) {
        if (Test-Path (Join-Path $RepoRoot "package-lock.json")) {
            & npm.cmd ci
        } else {
            & npm.cmd install
        }
        if ($LASTEXITCODE -ne 0) {
            throw "npm dependency installation failed with exit code $LASTEXITCODE"
        }
    }

    & npm.cmd run build:userscript-compiler
    if ($LASTEXITCODE -ne 0) {
        throw "userscript compiler build failed with exit code $LASTEXITCODE"
    }

    & python scripts\provision_bridge_token.py
    if ($LASTEXITCODE -ne 0) {
        throw "bridge token provisioning failed with exit code $LASTEXITCODE"
    }

    & python -m compileall -q pasi_bridge scripts\provision_bridge_token.py scripts\verify_bridge.py scripts\run_bridge.py
    if ($LASTEXITCODE -ne 0) {
        throw "bridge Python compilation check failed with exit code $LASTEXITCODE"
    }

    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$manifest.name)) {
        throw "manifest.json has no extension name"
    }
    if ([string]$manifest.manifest_version -ne "3") {
        throw "Expected a Manifest V3 extension"
    }
    if ([string]$manifest.background.service_worker -ne "src/background.js") {
        throw "Unexpected background service worker path"
    }

    $backgroundPath = Join-Path $extensionRoot "src\background.js"
    $backgroundText = Get-Content $backgroundPath -Raw
    if ($backgroundText -notmatch [regex]::Escape("http://127.0.0.1:8765")) {
        throw "Extension background does not reference the canonical bridge http://127.0.0.1:8765"
    }

    $gitSha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to determine source commit SHA"
    }

    $buildManifest = [ordered]@{
        source_commit = $gitSha
        extension_name = [string]$manifest.name
        extension_version = [string]$manifest.version
        manifest_version = [int]$manifest.manifest_version
        bridge_url = "http://127.0.0.1:8765"
        source_extension = $extensionRoot
        built_at_utc = [DateTime]::UtcNow.ToString("o")
    }

    Write-Host "PASI extension build complete."
    Write-Host "  source commit : $gitSha"
    Write-Host "  extension     : $($manifest.name)"
    Write-Host "  version       : $($manifest.version)"
    Write-Host "  bridge        : http://127.0.0.1:8765"
    Write-Host "  source        : $extensionRoot"
}
finally {
    Pop-Location
}
