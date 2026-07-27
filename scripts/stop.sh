#!/usr/bin/env bash
# Stop the dev services. Containers keep running unless --all is given.
#
#   scripts/stop.sh          backend, worker, frontend
#   scripts/stop.sh --all    also stop postgres and redis
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5300}"

stop_all_containers=false
[[ "${1:-}" == "--all" || "${1:-}" == "-a" ]] && stop_all_containers=true

# Never signal this script, its parent shell, or anything else in our own tree.
own_tree() {
  local pid=$$
  while [[ -n "$pid" && "$pid" != 1 ]]; do
    echo "$pid"
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  done
}
readarray -t PROTECTED < <(own_tree)

is_protected() {
  local pid=$1
  for p in "${PROTECTED[@]}"; do [[ "$pid" == "$p" ]] && return 0; done
  return 1
}

# TERM, then KILL whatever is still alive after the grace period.
terminate() {
  local label=$1 && shift
  local pids=()
  for pid in "$@"; do is_protected "$pid" || pids+=("$pid"); done
  if [[ ${#pids[@]} -eq 0 ]]; then
    printf '  %-10s not running\n' "$label"
    return
  fi

  kill "${pids[@]}" 2>/dev/null
  for _ in {1..20}; do
    local alive=()
    for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive+=("$pid"); done
    [[ ${#alive[@]} -eq 0 ]] && { printf '  %-10s stopped (%s)\n' "$label" "${pids[*]}"; return; }
    sleep 0.25
  done

  kill -9 "${pids[@]}" 2>/dev/null
  printf '  %-10s killed after timeout (%s)\n' "$label" "${pids[*]}"
}

pids_on_port() {
  # ss shows the owner of a listening socket; fall back to lsof where it does not.
  ss -ltnp "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u
  command -v lsof >/dev/null && lsof -ti "tcp:$1" -s TCP:LISTEN 2>/dev/null
}

echo "Stopping dev services in $ROOT"
readarray -t backend < <(pids_on_port "$BACKEND_PORT")
terminate backend "${backend[@]}"

readarray -t frontend < <(pids_on_port "$FRONTEND_PORT")
terminate frontend "${frontend[@]}"

# The worker holds no port, so match its command line.
readarray -t worker < <(pgrep -f 'arq app\.workers' 2>/dev/null)
terminate worker "${worker[@]}"

if $stop_all_containers; then
  echo "Stopping containers"
  docker compose -f "$ROOT/docker-compose.yml" down
else
  echo "Containers left running (use --all to stop postgres and redis)"
fi
