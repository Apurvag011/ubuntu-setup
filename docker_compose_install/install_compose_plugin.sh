#!/bin/bash
# install_compose_plugin.sh
# Installs the existing docker-compose binary as the Docker Compose CLI plugin
# No internet connection required — uses the local binary.

set -e

PLUGIN_NAME="docker-compose"  # results in `docker compose` command

BINARY_PATH="$PWD/docker-compose"

# Verify the binary exists before doing anything
if [ ! -f "$BINARY_PATH" ]; then
    echo "ERROR: Binary not found at '$BINARY_PATH'."
    echo "Please make sure docker-compose binary is in the same directory as this script."
    exit 1
fi

# Ensure it's executable before running version check
chmod +x "$BINARY_PATH"

# Verify it's a valid V2 binary by running --version directly
echo "Verifying downloaded binary..."
COMPOSE_VERSION=$("$BINARY_PATH" version 2>/dev/null || true)

if [ -z "$COMPOSE_VERSION" ]; then
    echo "ERROR: '$BINARY_PATH' does not appear to be a valid Docker Compose binary."
    exit 1
fi

echo "Found: $COMPOSE_VERSION"

# Warn if it looks like V1 (Python-based, not usable as a CLI plugin)
if echo "$COMPOSE_VERSION" | grep -qv "^Docker Compose version v2"; then
    echo "WARNING: This may not be a V2 binary. Only V2 works as a Docker CLI plugin."
    echo "Proceeding anyway..."
fi

echo "Binary OK at: $BINARY_PATH"

# ---------------------------------------------------------------
# 1. System-wide plugin directory (requires sudo)
# ---------------------------------------------------------------
SYSTEM_PLUGIN_DIR="/usr/local/lib/docker/cli-plugins"

echo "Creating plugin directory: $SYSTEM_PLUGIN_DIR"
sudo mkdir -p "$SYSTEM_PLUGIN_DIR"

PLUGIN_DEST="$SYSTEM_PLUGIN_DIR/$PLUGIN_NAME"

echo "Copying binary to: $PLUGIN_DEST"
sudo cp "$BINARY_PATH" "$PLUGIN_DEST"

echo "Setting permissions..."
sudo chmod +x "$PLUGIN_DEST"

# ---------------------------------------------------------------
# 2. Also install to user-level plugin directory (no sudo needed)
#    so it works for the current user even without system perms.
# ---------------------------------------------------------------
USER_PLUGIN_DIR="$HOME/.docker/cli-plugins"
echo "Creating user plugin directory: $USER_PLUGIN_DIR"
mkdir -p "$USER_PLUGIN_DIR"

USER_PLUGIN_DEST="$USER_PLUGIN_DIR/$PLUGIN_NAME"
echo "Copying binary to: $USER_PLUGIN_DEST"
cp "$BINARY_PATH" "$USER_PLUGIN_DEST"
chmod +x "$USER_PLUGIN_DEST"

# ---------------------------------------------------------------
# 3. Verify
# ---------------------------------------------------------------
echo ""
echo "--------------------------------------"
echo "Verifying installation..."
echo "--------------------------------------"

if docker compose version &>/dev/null; then
    docker compose version
    echo ""
    echo "SUCCESS: 'docker compose' plugin is working."
else
    echo "WARNING: 'docker compose' command not responding as expected."
    echo "Check that the binary is a valid Docker Compose V2 plugin."
    echo "Plugin locations:"
    echo "  System : $PLUGIN_DEST"
    echo "  User   : $USER_PLUGIN_DEST"
    exit 1
fi

echo ""
echo "Plugin installed to:"
echo "  System-wide : $PLUGIN_DEST"
echo "  User-level  : $USER_PLUGIN_DEST"
