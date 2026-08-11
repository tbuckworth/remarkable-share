#!/bin/bash
# Install the native messaging host for the Send to reMarkable extension.
# Run this AFTER loading the extension in chrome://extensions.
#
# Usage: ./install.sh <extension-id>
#   Get the extension ID from chrome://extensions after loading unpacked.

set -e

EXT_ID="${1:?Usage: ./install.sh <extension-id-from-chrome>}"
HOST_NAME="com.titus.web2pdf"
HOST_DIR="$(cd "$(dirname "$0")/native-host" && pwd)"
HOST_PATH="$HOST_DIR/web2pdf_host.py"
MANIFEST_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"

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
echo "  Extension: $EXT_ID"
echo ""
echo "Restart Chrome for changes to take effect."
