#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ $# -gt 0 ]]; then
	PORT="$1"
else
	PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(('', 0))
port = s.getsockname()[1]
s.close()
print(port)
PY
)"
fi

printf 'Serving %s at http://localhost:%s\n' "$ROOT" "$PORT"
exec python3 -m http.server "$PORT"
