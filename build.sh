#!/usr/bin/env bash
# Portable Linux build via PyInstaller.
#
# Output: dist/pymibbrowser-linux-x86_64/   (a folder; no system Python needed to run)
# Ship with:
#   tar czf pymibbrowser-linux-x86_64.tar.gz -C dist pymibbrowser-linux-x86_64/
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(pwd)"
VENV="$ROOT/.venv"
DIST="$ROOT/dist/pymibbrowser-linux-x86_64"
BUILD_VENV="$ROOT/.build-venv"

echo "=== 1/5 setting up build venv"
# Use a SEPARATE venv for build so PyInstaller + its hooks don't collide
# with the runtime venv. Cached between builds.
if [ ! -x "$BUILD_VENV/bin/python" ]; then
    python3 -m venv "$BUILD_VENV"
    "$BUILD_VENV/bin/pip" install -q --upgrade pip
fi
"$BUILD_VENV/bin/pip" install -q -r requirements.txt pyinstaller

echo "=== 2/5 cleaning previous build"
rm -rf build/ dist/

echo "=== 3/5 running PyInstaller"
"$BUILD_VENV/bin/pyinstaller" \
    --noconfirm \
    --clean \
    --name pymibbrowser \
    --windowed \
    --add-data "mibs-src:mibs-src" \
    --add-data "samples:samples" \
    --collect-submodules pysnmp \
    --collect-submodules pysmi \
    --collect-data pysnmp \
    --collect-data pysmi \
    entrypoint.py

# PyInstaller writes to dist/pymibbrowser/; rename for clarity.
mv dist/pymibbrowser "$DIST"

echo "=== 4/5 adding README / LICENSE / run.sh"
cp README.md LICENSE "$DIST/"
cat > "$DIST/run.sh" <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
exec ./pymibbrowser "$@"
EOF
chmod +x "$DIST/run.sh"

echo "=== 5/5 done"
du -sh "$DIST"
echo
echo "Output: $DIST"
echo "Launch: $DIST/pymibbrowser   (or $DIST/run.sh)"
echo "Ship:   tar czf pymibbrowser-linux-x86_64.tar.gz -C dist pymibbrowser-linux-x86_64/"
