[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

$resolvedRoot = (Resolve-Path $RepoRoot).Path

function Try-NativeGitSha {
    param([string]$Root)

    $output = & git -C $Root rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$output)) {
        return ([string]$output).Trim()
    }

    return $null
}

$gitSha = Try-NativeGitSha -Root $resolvedRoot
if (-not [string]::IsNullOrWhiteSpace($gitSha)) {
    Write-Output $gitSha
    exit 0
}

$wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($null -ne $wslCommand) {
    $wslRootOutput = & wsl.exe wslpath -a -u -- "$resolvedRoot" 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$wslRootOutput)) {
        $wslRoot = ([string]$wslRootOutput).Trim()
        $wslShaOutput = & wsl.exe git -C "$wslRoot" rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$wslShaOutput)) {
            Write-Output ([string]$wslShaOutput).Trim()
            exit 0
        }
    }
}

throw "Unable to determine source commit SHA for repository: $resolvedRoot. Native Git could not resolve it and WSL Git fallback was unavailable or failed."
