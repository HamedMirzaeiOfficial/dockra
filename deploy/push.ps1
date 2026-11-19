<#
.SYNOPSIS
  Ships the current commit of this repo to the Dockra server and upgrades the platform.

.EXAMPLE
  .\deploy\push.ps1 "fix login page"
  Commits pending changes with that message, pushes to GitHub, then updates the server.

.EXAMPLE
  .\deploy\push.ps1
  Ships HEAD as is (fails if there are uncommitted changes).
#>
param(
    [Parameter(Position = 0)][string]$Message = "",
    [string]$Server = $env:DOCKRA_SERVER,
    [int]$Port = $(if ($env:DOCKRA_SSH_PORT) { [int]$env:DOCKRA_SSH_PORT } else { 22 }),
    [string]$User = $(if ($env:DOCKRA_SSH_USER) { $env:DOCKRA_SSH_USER } else { "root" }),
    [string]$Key = $(if ($env:DOCKRA_SSH_KEY) { $env:DOCKRA_SSH_KEY } else { Join-Path $HOME ".ssh\dockra_deploy" }),
    [switch]$NoGitPush
)

$ErrorActionPreference = "Stop"
if (-not $Server) { throw "Set the target host with -Server or the DOCKRA_SERVER environment variable." }
# The server prints UTF-8; Windows PowerShell 5 consoles default to a legacy code page.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}

if (git status --porcelain) {
    if (-not $Message) {
        throw "There are uncommitted changes. Pass a commit message: .\deploy\push.ps1 `"what changed`""
    }
    Invoke-Native git @("add", "-A")
    Invoke-Native git @("commit", "-m", $Message)
}

if (-not $NoGitPush) {
    & git push
    if ($LASTEXITCODE -ne 0) { Write-Warning "git push failed; the server is updated anyway." }
}

$commit = (git rev-parse --short HEAD).Trim()
$archive = Join-Path ([IO.Path]::GetTempPath()) "dockra-release-$commit.tar.gz"
# Server scripts need LF line endings even when core.autocrlf is on.
Invoke-Native git @("-c", "core.autocrlf=false", "archive", "--format=tar.gz", "-o", $archive, "HEAD")

$sshOptions = @("-p", "$Port", "-o", "StrictHostKeyChecking=accept-new")
if (Test-Path $Key) { $sshOptions += @("-i", $Key, "-o", "IdentitiesOnly=yes") }
$target = "$User@$Server"
$remote = "/tmp/dockra-release-$commit.tar.gz"

try {
    Write-Host "Uploading $commit to $Server ..."
    $scpOptions = $sshOptions -replace "^-p$", "-P"
    Invoke-Native scp (@("-q") + $scpOptions + @($archive, "${target}:$remote"))
    Write-Host "Updating the platform on the server ..."
    Invoke-Native ssh ($sshOptions + @($target, "tar -xzOf $remote deploy/update.sh | bash -s $remote $commit"))
}
finally {
    Remove-Item $archive -ErrorAction SilentlyContinue
}
Write-Host "Done: $commit is live on $Server"
