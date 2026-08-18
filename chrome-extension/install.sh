#!/bin/bash
# Install the native messaging host for the Send to reMarkable extension.
#
# Usage: ./install.sh [extension-id ...]
#
# Accepts several IDs, because the same checkout can be loaded by more than one
# route (e.g. a local path and an SMB mount of the same repo), and each route is
# a different extension as far as Chrome is concerned.
#
# With no argument the extension ID is computed the same way Chrome computes it,
# so the two can never disagree:
#   - if manifest.json has a "key", the ID is derived from that key (stable
#     wherever this directory lives);
#   - otherwise Chrome derives it from the absolute install path, and so do we.
#
# The path-derived case is why moving this directory breaks things: the ID
# changes, the host stops recognising the extension, and the old registration in
# chrome://extensions points at a directory that no longer exists. Re-running
# this script after any move is the fix.
#
# Pass an ID explicitly only to override, e.g. to match what chrome://extensions
# actually shows.

set -euo pipefail

HOST_NAME="com.titus.web2pdf"
# -P resolves symlinks: Chrome hashes the real path, so a symlinked parent would
# otherwise give us an ID that doesn't match the one it shows.
EXT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
HOST_PATH="$EXT_DIR/native-host/web2pdf_host.py"
MANIFEST_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"

if [ ! -f "$HOST_PATH" ]; then
  echo "error: native host not found at $HOST_PATH" >&2
  exit 1
fi

EXT_IDS=()
if [ $# -gt 0 ]; then
  for arg in "$@"; do
    [ -n "$arg" ] && EXT_IDS+=("$arg")
  done
  ID_SOURCE="arguments"
else
  # Assign to a variable first: a failing python3 inside a here-string feeding
  # `read` would not trip set -e, and we would register an empty ID.
  ID_OUTPUT="$(python3 - "$EXT_DIR" <<'PY'
import base64, binascii, hashlib, json, os, sys

ext_dir = sys.argv[1]
manifest = os.path.join(ext_dir, "manifest.json")
try:
    key = json.load(open(manifest)).get("key")
except (OSError, ValueError) as e:
    sys.exit("cannot read %s: %s" % (manifest, e))

if key:
    try:
        seed, source = base64.b64decode(key, validate=True), "manifest key"
    except (binascii.Error, ValueError) as e:
        sys.exit('manifest "key" is not valid base64: %s' % e)
else:
    seed, source = ext_dir.encode(), "install path"

digest = hashlib.sha256(seed).hexdigest()[:32]
print("".join(chr(ord("a") + int(c, 16)) for c in digest), source)
PY
)"
  read -r DERIVED_ID ID_SOURCE <<<"$ID_OUTPUT"
  EXT_IDS=("$DERIVED_ID")
fi

if [ ${#EXT_IDS[@]} -eq 0 ] || [ -z "${EXT_IDS[0]}" ]; then
  echo "error: could not determine the extension ID" >&2
  exit 1
fi

ORIGINS=""
for id in "${EXT_IDS[@]}"; do
  [ -n "$ORIGINS" ] && ORIGINS="$ORIGINS,"
  ORIGINS="$ORIGINS
    \"chrome-extension://$id/\""
done

mkdir -p "$MANIFEST_DIR"

cat > "$MANIFEST_DIR/$HOST_NAME.json" <<EOF
{
  "name": "$HOST_NAME",
  "description": "Send to reMarkable native messaging host",
  "path": "$HOST_PATH",
  "type": "stdio",
  "allowed_origins": [$ORIGINS
  ]
}
EOF

echo "Installed native messaging host:"
echo "  Manifest: $MANIFEST_DIR/$HOST_NAME.json"
echo "  Host: $HOST_PATH"
echo "  Extensions (from $ID_SOURCE):"
for id in "${EXT_IDS[@]}"; do echo "    $id"; done
echo ""
echo "Restart Chrome for changes to take effect."
