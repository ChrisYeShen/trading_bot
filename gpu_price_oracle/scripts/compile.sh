#!/usr/bin/env bash
# Compile GPUPriceOracle with solc.
# Install solc: pip install py-solc-x && python -c "from solcx import install_solc; install_solc('0.8.20')"
# Or install system solc >= 0.8.20.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
CONTRACTS="$ROOT/contracts"
ARTIFACTS="$ROOT/artifacts"

mkdir -p "$ARTIFACTS"

echo "Compiling GPUPriceOracle.sol..."
solc \
  --abi \
  --bin \
  --optimize \
  --optimize-runs 200 \
  --overwrite \
  -o "$ARTIFACTS" \
  "$CONTRACTS/GPUPriceOracle.sol"

# Rename output to predictable names
mv -f "$ARTIFACTS/GPUPriceOracle.abi" "$ARTIFACTS/GPUPriceOracle.abi.json" 2>/dev/null || true
mv -f "$ARTIFACTS/GPUPriceOracle.bin" "$ARTIFACTS/GPUPriceOracle.bin" 2>/dev/null || true

echo "Done. Artifacts written to $ARTIFACTS/"
ls -lh "$ARTIFACTS/"
