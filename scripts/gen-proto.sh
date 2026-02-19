#!/usr/bin/env bash
# Generate Python gRPC stubs from navigator.proto
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROTO_DIR="$PROJECT_DIR/cybersec/engine/proto"
OUT_DIR="$PROJECT_DIR/cybersec/engine/generated"

mkdir -p "$OUT_DIR"

echo "Generating gRPC stubs from $PROTO_DIR/navigator.proto..."

# Use uv run if available (grpcio-tools lives in the uv venv)
PYTHON="python"
if command -v uv &>/dev/null && [ -f "$PROJECT_DIR/pyproject.toml" ]; then
  PYTHON="uv run python"
fi

PROTO_INCLUDE=$($PYTHON -c 'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')

$PYTHON -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  -I "$PROTO_INCLUDE" \
  --python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  "$PROTO_DIR/navigator.proto"

# Fix imports in generated code (protoc generates absolute imports)
sed -i 's/^import navigator_pb2/from . import navigator_pb2/' "$OUT_DIR/navigator_pb2_grpc.py"

echo "Generated:"
ls -la "$OUT_DIR"/navigator_pb2*.py*
echo "Done."
