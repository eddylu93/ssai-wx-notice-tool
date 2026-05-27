#!/usr/bin/env bash
set -euo pipefail

APP_NAME="SSAI-WX 通知小工具"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller

.venv/bin/pyinstaller --noconfirm "SSAI-WX 通知小工具.spec"

mkdir -p release/macos
rm -f "release/macos/${APP_NAME}_macOS.dmg"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/${APP_NAME}.app" \
  -ov \
  -format UDZO \
  "release/macos/${APP_NAME}_macOS.dmg"

echo "Build complete: release/macos/${APP_NAME}_macOS.dmg"

