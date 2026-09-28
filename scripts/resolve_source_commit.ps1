[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

$resolvedRoot = (Resolve-Path $RepoRoot).Path

# This checkout is operated from WSL. Windows Git cannot reliably resolve
# the linked-worktree metadata, so resolve the source commit through WSL Git.
$wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($null -eq $wslCommand) {
    throw "WSL is required to resolve the source commit for this checkout: $resolvedRoot"
}

if ($resolvedRoot -notmatch '^(?<drive>[A-Za-z]):\\(?<path>.+)$') {
    throw "Repository path is not a Windows drive path that can be mapped into WSL: $resolvedRoot"
}

$drive = $Matches.drive.ToLowerInvariant()
$relative = $Matches.path.Replace([char]92, [char]47)
$wslRoot = "/mnt/$drive/$relative"

$wslShaOutput = & wsl.exe -e git -C "$wslRoot" rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$wslShaOutput)) {
    throw "Unable to determine source commit SHA through WSL Git for repository: $resolvedRoot"
}

Write-Output ([string]$wslShaOutput).Trim()
