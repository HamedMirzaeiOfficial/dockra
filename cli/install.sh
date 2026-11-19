#!/bin/sh
# Run once so the dockra command is available from any folder.
set -eu
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
python3 -m pip install --user -e "$here"
scripts=$(python3 -c "import sysconfig; print(sysconfig.get_path('scripts', 'posix_user'))")
case ":$PATH:" in
  *":$scripts:"*) ;;
  *)
    echo "Add this folder to your PATH: $scripts"
    ;;
esac
echo "Done. In your project folder: dockra login --username admin"
