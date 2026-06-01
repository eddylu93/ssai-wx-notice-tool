#!/usr/bin/env bash
set -euo pipefail

APP_NAME="SSAI-WX 通知小工具"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
APP_VERSION="$(python3 - <<'PY'
import re
from pathlib import Path

match = re.search(r'^APP_VERSION\s*=\s*"V?([^"]+)"', Path("app.py").read_text(encoding="utf-8"), re.MULTILINE)
if not match:
    raise SystemExit("APP_VERSION not found")
print(match.group(1))
PY
)"
ARTIFACT_PREFIX="SSAI-WX-Notice-Tool-V${APP_VERSION}-macOS-arm64"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller

.venv/bin/pyinstaller --noconfirm "SSAI-WX 通知小工具.spec"

mkdir -p release/macos
rm -f \
  "release/macos/${APP_NAME}_macOS.dmg" \
  "release/macos/${ARTIFACT_PREFIX}.dmg" \
  "release/macos/${ARTIFACT_PREFIX}.pkg"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/${APP_NAME}.app" \
  -ov \
  -format UDZO \
  "release/macos/${APP_NAME}_macOS.dmg"
cp "release/macos/${APP_NAME}_macOS.dmg" "release/macos/${ARTIFACT_PREFIX}.dmg"

productbuild \
  --component "dist/${APP_NAME}.app" \
  /Applications \
  "release/macos/${ARTIFACT_PREFIX}.pkg"

echo "Build complete:"
echo "  release/macos/${ARTIFACT_PREFIX}.dmg"
echo "  release/macos/${ARTIFACT_PREFIX}.pkg"
