[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

$resolvedRoot = (Resolve-Path $RepoRoot).Path

# This checkout is operated from WSL. Its .git metadata can be a WSL-linked
# worktree path that Windows Git cannot resolve, producing "not a git repository:
# (NULL)". Resolve the source commit through WSL Git instead of invoking native
# Windows Git on the WSL-backed worktree.
$wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($null -eq $wslCommand) {
    throw "WSL is required to resolve the source commit for this checkout: $resolvedRoot"
}

$wslRootOutput = & wsl.exe wslpath -a -u -- "$resolvedRoot" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$wslRootOutput)) {
    throw "Unable to convert repository path to WSL form: $resolvedRoot"
}

$wslRoot = ([string]$wslRootOutput).Trim()
$wslShaOutput = & wsl.exe -e git -C "$wslRoot" rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$wslShaOutput)) {
    throw "Unable to determine source commit SHA through WSL Git for repository: $resolvedRoot"
}

Write-Output ([string]$wslShaOutput).Trim()
