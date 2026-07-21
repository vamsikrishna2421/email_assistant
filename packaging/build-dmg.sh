#!/usr/bin/env bash
#
# Builds Jerry.app and wraps it in a drag-to-install .dmg.
#
#   ./packaging/build-dmg.sh
#
# The result is unsigned. macOS quarantines downloaded unsigned apps and shows
# "Jerry is damaged and can't be opened", which reads as a broken download
# rather than a security prompt — so this is fine for installing locally and NOT
# fine for sending to anyone. See docs/SHIPPING.md for signing and notarising.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/packaging"

echo "==> building app"
"$ROOT/.venv/bin/pyinstaller" --noconfirm --clean jerry.spec >/dev/null

STAGE="$(mktemp -d)"
cp -R dist/Jerry.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo "==> building disk image"
rm -f dist/Jerry.dmg
hdiutil create -volname "Jerry" -srcfolder "$STAGE" -ov -format UDZO \
  -quiet dist/Jerry.dmg
rm -rf "$STAGE"

echo "==> done: packaging/dist/Jerry.dmg ($(du -h dist/Jerry.dmg | cut -f1))"
