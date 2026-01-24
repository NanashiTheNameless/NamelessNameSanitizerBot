#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ $# -gt 0 ]]; then
	PORT="$1"
else
	PORT=8000
	# Find first available port from 8000 to 8100
	while (( PORT <= 8100 )); do
		if python3 -c "import socket; s = socket.socket(); s.bind(('', $PORT)); s.close()" 2>/dev/null; then
			break
		fi
		(( PORT++ ))
	done
fi

printf 'Serving %s at http://localhost:%s\n' "$ROOT" "$PORT"
exec python3 -m http.server "$PORT"
