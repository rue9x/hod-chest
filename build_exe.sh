#!/usr/bin/env bash
# Build hod-chest.exe (Windows) on Linux, with Windows Python and PyInstaller running under Wine.
#
#   ./build_exe.sh                 -> dist/hod-chest.exe
#
# Needs wine and curl. The first run downloads the official Windows Python installer from
# python.org and sets up a private Wine prefix in ~/.cache/hod-chest-wine (your own ~/.wine is
# not touched); later runs reuse it. On Windows itself: pip install pyinstaller, then
# pyinstaller hod-chest.spec.
set -euo pipefail

PYVER=3.12.8
HERE="$(cd "$(dirname "$0")" && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/hod-chest-wine"
export WINEPREFIX="$CACHE/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
PY='C:\Python312\python.exe'

mkdir -p "$CACHE"
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
    echo "Setting up Windows Python $PYVER under Wine (first run only)..."
    installer="$CACHE/python-$PYVER-amd64.exe"
    [ -f "$installer" ] || curl -sSL -o "$installer" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe"
    wineboot -i >/dev/null 2>&1
    wine "$installer" /quiet InstallAllUsers=0 'TargetDir=C:\Python312' Include_test=0 Include_doc=0 \
        Include_tcltk=0 Include_launcher=0 Shortcuts=0 AssociateFiles=0 PrependPath=0 >/dev/null 2>&1
    wine "$PY" -m pip install -q --disable-pip-version-check --no-warn-script-location pyinstaller
fi

# Build from a clean copy, so build files never land in the project.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/cheats"
cp "$HERE"/*.py "$HERE/hod-chest.spec" "$work/"
cp -r "$HERE/web" "$work/"
cp "$HERE"/cheats/*.py "$HERE/cheats/positions_default.json" "$work/cheats/"
(cd "$work" && wine "$PY" -m PyInstaller --noconfirm hod-chest.spec >/dev/null 2>&1)

mkdir -p "$HERE/dist"
cp "$work/dist/hod-chest.exe" "$HERE/dist/hod-chest.exe"
echo "Built $HERE/dist/hod-chest.exe"
