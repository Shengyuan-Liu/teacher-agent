#!/usr/bin/env bash
# Start the dev stack.
#
#   scripts/dev.sh            foreground, Ctrl+C stops everything
#   scripts/dev.sh --nohup    detached, logs under logs/run_logs/
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5300}"
LOG_DIR="$ROOT/logs/run_logs"

detach=false
[[ "${1:-}" == "--nohup" || "${1:-}" == "-d" ]] && detach=true

# ss on Linux, lsof on macOS which has no ss.
port_busy() {
  if command -v ss >/dev/null; then
    ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
  else
    lsof -ti "tcp:$1" -s TCP:LISTEN >/dev/null 2>&1
  fi
}

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if port_busy "$port"; then
    echo "Port $port is already in use — run scripts/stop.sh first." >&2
    exit 1
  fi
done

docker compose -f "$ROOT/docker-compose.yml" up -d || exit 1

backend_cmd=(uv run uvicorn app.main:app --reload --port "$BACKEND_PORT")
worker_cmd=(uv run arq app.workers.main.WorkerSettings)
frontend_cmd=(pnpm dev)

if $detach; then
  stamp="$(date +%Y-%m-%d_%H-%M-%S)"
  mkdir -p "$LOG_DIR"
  # setsid gives each service its own session so it survives this shell closing.
  # macOS has no setsid; a nohup'd child in a subshell that exits reparents to
  # init, which achieves the same thing.
  command -v setsid >/dev/null && detacher=setsid || detacher=
  (cd "$ROOT/backend" && $detacher nohup "${backend_cmd[@]}" \
     > "$LOG_DIR/$stamp-backend.log" 2>&1 < /dev/null &)
  (cd "$ROOT/backend" && $detacher nohup "${worker_cmd[@]}" \
     > "$LOG_DIR/$stamp-worker.log" 2>&1 < /dev/null &)
  (cd "$ROOT/frontend" && $detacher nohup "${frontend_cmd[@]}" \
     > "$LOG_DIR/$stamp-frontend.log" 2>&1 < /dev/null &)

  echo "Started in the background. Logs:"
  for service in backend worker frontend; do
    printf '  %-9s %s\n' "$service" "logs/run_logs/$stamp-$service.log"
  done
  echo
  echo "  follow:  tail -f logs/run_logs/$stamp-*.log"
  echo "  stop:    make stop"
  exit 0
fi

# Foreground: one Ctrl+C takes the whole stack down.
pids=()
cleanup() {
  trap - INT TERM
  [[ ${#pids[@]} -gt 0 ]] && kill "${pids[@]}" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

(cd "$ROOT/backend" && exec "${backend_cmd[@]}") & pids+=($!)
(cd "$ROOT/backend" && exec "${worker_cmd[@]}") & pids+=($!)
(cd "$ROOT/frontend" && exec "${frontend_cmd[@]}") & pids+=($!)
wait
