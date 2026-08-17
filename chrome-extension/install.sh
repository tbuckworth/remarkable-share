#!/bin/bash
# Install the native messaging host for the Send to reMarkable extension.
#
# Usage: ./install.sh [extension-id]
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

set -e

HOST_NAME="com.titus.web2pdf"
EXT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_PATH="$EXT_DIR/native-host/web2pdf_host.py"
MANIFEST_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"

if [ -n "$1" ]; then
  EXT_ID="$1"
  ID_SOURCE="argument"
else
  read -r EXT_ID ID_SOURCE <<<"$(python3 - "$EXT_DIR" <<'PY'
import base64, hashlib, json, os, sys

ext_dir = sys.argv[1]
key = json.load(open(os.path.join(ext_dir, "manifest.json"))).get("key")
if key:
    seed, source = base64.b64decode(key), "manifest key"
else:
    seed, source = ext_dir.encode(), "install path"
digest = hashlib.sha256(seed).hexdigest()[:32]
print("".join(chr(ord("a") + int(c, 16)) for c in digest), source)
PY
)"
fi

mkdir -p "$MANIFEST_DIR"

cat > "$MANIFEST_DIR/$HOST_NAME.json" <<EOF
{
  "name": "$HOST_NAME",
  "description": "Send to reMarkable native messaging host",
  "path": "$HOST_PATH",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXT_ID/"
  ]
}
EOF

echo "Installed native messaging host:"
echo "  Manifest: $MANIFEST_DIR/$HOST_NAME.json"
echo "  Host: $HOST_PATH"
echo "  Extension: $EXT_ID  (from $ID_SOURCE)"
echo ""
echo "Restart Chrome for changes to take effect."
