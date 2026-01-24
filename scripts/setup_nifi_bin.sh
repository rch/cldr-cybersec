#!/usr/bin/env bash
# Download and setup NiFi binary distribution
# Usage: ./scripts/setup_nifi_bin.sh [version]
#
# This script downloads Apache NiFi binary distribution to thirdparty/nifi/
# for use with devenv. NiFi 2.0+ requires Java 21+ which is already
# configured in devenv.nix.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

NIFI_VERSION="${1:-2.0.0}"
NIFI_DIR="$PROJECT_ROOT/thirdparty/nifi"
NIFI_HOME="$NIFI_DIR/nifi-${NIFI_VERSION}"
DOWNLOAD_URL="https://archive.apache.org/dist/nifi/${NIFI_VERSION}/nifi-${NIFI_VERSION}-bin.zip"

echo "======================================================"
echo "  NiFi Binary Setup"
echo "======================================================"
echo ""
echo "  Version:  $NIFI_VERSION"
echo "  Target:   $NIFI_HOME"
echo ""

# Check if already installed
if [ -d "$NIFI_HOME" ]; then
    echo "NiFi $NIFI_VERSION already installed at $NIFI_HOME"
    echo ""
    echo "To reinstall, remove the directory first:"
    echo "  rm -rf $NIFI_HOME"
    exit 0
fi

# Check for required tools
if ! command -v curl &> /dev/null; then
    echo "ERROR: curl is required but not installed"
    exit 1
fi

if ! command -v unzip &> /dev/null; then
    echo "ERROR: unzip is required but not installed"
    exit 1
fi

# Check Java version (NiFi 2.0+ requires Java 21+)
if command -v java &> /dev/null; then
    JAVA_VERSION=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
    if [ "$JAVA_VERSION" -lt 21 ] 2>/dev/null; then
        echo "WARNING: NiFi $NIFI_VERSION requires Java 21+"
        echo "         Current Java version: $JAVA_VERSION"
        echo "         devenv should provide the correct Java version"
    fi
else
    echo "WARNING: Java not found in PATH"
    echo "         NiFi $NIFI_VERSION requires Java 21+"
fi

# Create directory
mkdir -p "$NIFI_DIR"
cd "$NIFI_DIR"

# Download
echo "Downloading NiFi $NIFI_VERSION..."
echo "  URL: $DOWNLOAD_URL"
echo ""

if ! curl -L -o "nifi-${NIFI_VERSION}-bin.zip" "$DOWNLOAD_URL"; then
    echo ""
    echo "ERROR: Download failed"
    echo ""
    echo "Alternative download locations:"
    echo "  - https://nifi.apache.org/download/"
    echo "  - https://archive.apache.org/dist/nifi/"
    exit 1
fi

# Verify download
if [ ! -f "nifi-${NIFI_VERSION}-bin.zip" ]; then
    echo "ERROR: Downloaded file not found"
    exit 1
fi

FILE_SIZE=$(stat -f%z "nifi-${NIFI_VERSION}-bin.zip" 2>/dev/null || stat -c%s "nifi-${NIFI_VERSION}-bin.zip" 2>/dev/null)
if [ "$FILE_SIZE" -lt 1000000 ]; then
    echo "ERROR: Downloaded file is too small (${FILE_SIZE} bytes)"
    echo "       The download may have failed or the version may not exist"
    rm -f "nifi-${NIFI_VERSION}-bin.zip"
    exit 1
fi

# Extract
echo ""
echo "Extracting..."
unzip -q "nifi-${NIFI_VERSION}-bin.zip"

# Cleanup
rm "nifi-${NIFI_VERSION}-bin.zip"

# Verify extraction
if [ ! -d "$NIFI_HOME" ]; then
    echo "ERROR: Extraction failed - $NIFI_HOME not found"
    exit 1
fi

if [ ! -f "$NIFI_HOME/bin/nifi.sh" ]; then
    echo "ERROR: NiFi binary not found at $NIFI_HOME/bin/nifi.sh"
    exit 1
fi

echo ""
echo "======================================================"
echo "  NiFi $NIFI_VERSION installed successfully!"
echo "======================================================"
echo ""
echo "  Location: $NIFI_HOME"
echo ""
echo "  To start NiFi with devenv:"
echo "    devenv up"
echo ""
echo "  To start NiFi manually:"
echo "    $NIFI_HOME/bin/nifi.sh start"
echo ""
echo "  Web UI will be available at:"
echo "    http://localhost:8450/nifi"
echo ""
