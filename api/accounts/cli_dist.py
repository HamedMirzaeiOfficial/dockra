"""Install and update the CLI from the server itself, without GitHub, PyPI or npm (works on isolated networks too).

dockra.py is read straight from the repository's cli folder and only needs the Python standard library.
Prebuilt binaries (for Windows without Python) are optional; an administrator places them in DOCKRA_CLI_BIN_DIR.
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

BINARIES = ("dockra-linux", "dockra-macos", "dockra-windows.exe")
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def _script() -> Path:
    return Path(settings.DOCKRA_CLI_DIR) / "dockra.py"


def _bin_dir() -> Path:
    raw = settings.DOCKRA_CLI_BIN_DIR
    if raw:
        return Path(raw)
    from engine.volumes import data_root

    return data_root() / "cli"


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def cli_version() -> str:
    path = _script()
    if not path.is_file():
        return ""
    match = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def _base(request) -> str:
    return request.build_absolute_uri("/").rstrip("/")


@require_GET
def cli_version_view(request):
    folder = _bin_dir()
    binaries = [name for name in BINARIES if (folder / name).is_file()]
    return JsonResponse({"version": cli_version(), "binaries": binaries})


@require_GET
def cli_script(request):
    path = _script()
    if not path.is_file():
        return JsonResponse({"detail": "The CLI file is not available on this server"}, status=404)
    response = FileResponse(path.open("rb"), content_type="text/x-python; charset=utf-8")
    response["X-Dockra-Sha256"] = _sha256(path)
    response["X-Dockra-Version"] = cli_version()
    return response


@require_GET
def cli_binary(request, name):
    if name not in BINARIES:
        return JsonResponse({"detail": "Unknown file"}, status=404)
    path = _bin_dir() / name
    if not path.is_file():
        return JsonResponse({"detail": f"{name} has not been uploaded to this server"}, status=404)
    response = FileResponse(path.open("rb"), as_attachment=True, filename=name, content_type="application/octet-stream")
    response["X-Dockra-Sha256"] = _sha256(path)
    return response


INSTALL_SH = r"""#!/bin/sh
# Install dockra from this Dockra server (Linux, macOS).
set -eu
BASE="__BASE__"
dest="${DOCKRA_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$dest"

fetch() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -q "$1" -O "$2"
  else echo "curl or wget is required" >&2; exit 1; fi
}

tmp="$dest/.dockra.download"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  fetch "$BASE/cli/dockra.py" "$tmp"
  python3 -c "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), 'dockra', 'exec')" "$tmp"
  how="python3"
else
  case "$(uname -s)" in
    Darwin) name=dockra-macos ;;
    *) name=dockra-linux ;;
  esac
  if ! fetch "$BASE/cli/bin/$name" "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "Python 3.10+ is required (or ask the admin to put $name on the server)." >&2
    exit 1
  fi
  how="$name"
fi
chmod +x "$tmp"
mv "$tmp" "$dest/dockra"

echo "dockra installed ($how): $dest/dockra"

on_path() { case ":$PATH:" in *":$1:"*) return 0 ;; esac; return 1; }

# ~/.local/bin is only put on PATH (e.g. by Ubuntu's ~/.profile) if it existed at login. Persist it in
# the shell startup files so new terminals find dockra.
if ! on_path "$dest"; then
  line="export PATH=\"$dest:\$PATH\""
  case "${SHELL:-}" in */zsh) touch "$HOME/.zshrc" ;; esac
  for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
    [ "$rc" = "$HOME/.profile" ] || [ -f "$rc" ] || continue
    grep -qsF "$line" "$rc" || printf '\n# dockra\n%s\n' "$line" >> "$rc"
  done
fi

# This script runs in a child of the user's shell (curl | sh), so it cannot change that shell's PATH.
# A link in a directory that is already on PATH makes dockra work in the same terminal right away.
ready=""
if on_path "$dest"; then
  ready=1
else
  for dir in /usr/local/bin /opt/homebrew/bin "$HOME/bin"; do
    if on_path "$dir" && [ -d "$dir" ] && [ -w "$dir" ] && ln -sf "$dest/dockra" "$dir/dockra"; then
      ready=1
      break
    fi
  done
  if [ -z "$ready" ] && on_path /usr/local/bin && command -v sudo >/dev/null 2>&1 && (: </dev/tty) 2>/dev/null; then
    echo "Linking /usr/local/bin/dockra so it works in this terminal (sudo may ask for your password):"
    if sudo mkdir -p /usr/local/bin </dev/tty && sudo ln -sf "$dest/dockra" /usr/local/bin/dockra </dev/tty; then
      ready=1
    fi
  fi
fi

echo
if [ -n "$ready" ]; then
  echo "Next:  dockra login --api $BASE"
else
  echo "Open a NEW terminal (this one does not see dockra yet), or first run here:"
  echo "  $line"
  echo "Then:  dockra login --api $BASE"
fi
"""

INSTALL_PS1 = r"""# Install dockra from this Dockra server (Windows).
$ErrorActionPreference = "Stop"
$Base = "__BASE__"
$Dir = Join-Path $env:LOCALAPPDATA "dockra"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

function Find-Python {
    # The Microsoft Store python shim writes to stderr; that must not abort the install.
    $ErrorActionPreference = "Continue"
    foreach ($cmd in @(@("py", "-3"), @("python"))) {
        if (Get-Command $cmd[0] -ErrorAction SilentlyContinue) {
            $extra = @()
            if ($cmd.Length -gt 1) { $extra = $cmd[1..($cmd.Length - 1)] }
            try {
                & $cmd[0] @extra -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { return ,$cmd }
            } catch { }
        }
    }
    return $null
}

$python = Find-Python
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Dir "dockra.exe"), (Join-Path $Dir "dockra.cmd")
if ($python) {
    Invoke-WebRequest -UseBasicParsing "$Base/cli/dockra.py" -OutFile (Join-Path $Dir "dockra.py")
    $runner = ($python -join " ")
    Set-Content -Encoding ASCII -Path (Join-Path $Dir "dockra.cmd") -Value @(
        "@echo off",
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
        "$runner `"%~dp0dockra.py`" %*"
    )
    $how = "Python"
} else {
    try {
        Invoke-WebRequest -UseBasicParsing "$Base/cli/bin/dockra-windows.exe" -OutFile (Join-Path $Dir "dockra.exe")
        $how = "dockra-windows.exe"
    } catch {
        Write-Host "Python 3.10+ is required, or ask the admin to put dockra-windows.exe on the server."
        exit 1
    }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$Dir*") {
    $joined = if ($userPath) { "$userPath;$Dir" } else { $Dir }
    [Environment]::SetEnvironmentVariable("Path", $joined, "User")
    $env:Path = "$env:Path;$Dir"
    Write-Host "Added to the user PATH; new terminals will find dockra."
}
Write-Host "dockra installed ($how): $Dir"
Write-Host "Next:  dockra login --api $Base"
"""


def _script_response(text: str, request, content_type: str) -> HttpResponse:
    return HttpResponse(text.replace("__BASE__", _base(request)), content_type=f"{content_type}; charset=utf-8")


@require_GET
def cli_install_sh(request):
    return _script_response(INSTALL_SH, request, "text/x-shellscript")


@require_GET
def cli_install_ps1(request):
    return _script_response(INSTALL_PS1, request, "text/plain")
