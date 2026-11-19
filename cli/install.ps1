# Install the dockra command for the current user.
# Run once from the repo:  .\cli\install.ps1
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot

function Invoke-DockraPip([string[]]$Extra) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m pip install @Extra -e $here | Out-Host
    } else {
        & python -m pip install @Extra -e $here | Out-Host
    }
    $pipCode = $LASTEXITCODE
    return $pipCode
}

$code = Invoke-DockraPip @()
if ($code -ne 0) {
    Write-Host "System install failed; installing for this user."
    $code = Invoke-DockraPip @("--user")
    if ($code -ne 0) { exit $code }
    $query = "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $scripts = & py -3 -c $query
    } else {
        $scripts = & python -c $query
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($scripts -and ($userPath -notlike "*${scripts}*")) {
        if ($userPath) { $joined = "$userPath;$scripts" } else { $joined = $scripts }
        [Environment]::SetEnvironmentVariable("Path", $joined, "User")
        Write-Host "Added to the user PATH. Open a new terminal:"
        Write-Host $scripts
    }
}

Write-Host "Done. From a project folder: dockra login --username admin"
exit 0
