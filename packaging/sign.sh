#!/usr/bin/env bash
#
# Signs Jerry.app with your Developer ID, then (optionally) notarises it.
#
#   ./packaging/sign.sh                 sign only
#   ./packaging/sign.sh --notarize      sign, submit to Apple, staple
#
# Signing order matters: every nested binary must be signed BEFORE the bundle
# that contains it, otherwise sealing the outer bundle invalidates the inner
# signatures and `codesign --verify` fails with a "code object is not signed at
# all" that points at the wrong file.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT/packaging/dist/Jerry.app}"
[ "${APP:0:2}" = "--" ] && APP="$ROOT/packaging/dist/Jerry.app"

IDENTITY="$(security find-identity -v -p codesigning | grep "Developer ID Application" | head -1 | sed 's/.*"\(.*\)"/\1/')"
if [ -z "$IDENTITY" ]; then
  echo "No Developer ID Application certificate found in your keychain." >&2
  exit 1
fi
TEAM_ID="$(echo "$IDENTITY" | sed -n 's/.*(\([A-Z0-9]*\))$/\1/p')"
echo "==> signing as: $IDENTITY"

ENT="$ROOT/packaging/entitlements.plist"

# Inner binaries first, deepest last-modified-safe order.
find "$APP" \( -name "*.dylib" -o -name "*.so" -o -perm +111 -type f \) -print0 2>/dev/null |
while IFS= read -r -d '' f; do
  case "$f" in
    *.dylib|*.so)
      codesign --force --timestamp --options runtime \
               --sign "$IDENTITY" "$f" 2>/dev/null || true ;;
  esac
done

# The main executable, then the bundle itself.
codesign --force --timestamp --options runtime --entitlements "$ENT" \
         --sign "$IDENTITY" "$APP/Contents/MacOS/Jerry"
codesign --force --timestamp --options runtime --entitlements "$ENT" \
         --sign "$IDENTITY" "$APP"

echo "==> verifying"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | sed 's/^/    /'

if [ "${2:-${1:-}}" = "--notarize" ]; then
  echo "==> notarising (a few minutes)"
  ZIP="$ROOT/packaging/dist/Jerry-notarize.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "jerry" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  echo "==> stapled. This app will now open on any Mac."
else
  echo
  echo "Signed but NOT notarised. Fine on this Mac; a Mac that downloads it"
  echo "will still warn. To notarise, once only:"
  echo
  echo "  xcrun notarytool store-credentials jerry \\"
  echo "     --apple-id <your-apple-id> --team-id $TEAM_ID --password <app-specific-password>"
  echo
  echo "then re-run:  ./packaging/sign.sh --notarize"
fi
