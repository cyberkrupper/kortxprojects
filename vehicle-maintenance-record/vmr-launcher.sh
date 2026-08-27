#!/usr/bin/env bash

set -u

VMR_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd -- "$VMR_SCRIPT_DIR" || exit 1

if ! command -v node >/dev/null 2>&1; then
  echo "VMR requires Node.js 18 or newer, but node was not found." >&2
  echo "Install Node.js, then run this launcher again." >&2
  exit 127
fi

exec node "$VMR_SCRIPT_DIR/vmr-launcher.js" "$@"
