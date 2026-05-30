#!/usr/bin/env bash
#
# dev.sh - Forza Horizon 6 Telemetry Development Environment Manager
#
# Starts, stops, and restarts the local development stack safely.
#
# Components managed:
#   - mongo     → MongoDB via docker compose (latest image)
#   - collector → The UDP telemetry collector (Python daemon)
#   - api       → Flask API (dev server on port 5003)
#   - frontend  → Vite React dev server (on port 3003)
#
# The Textual dashboard is intentionally NOT managed here (it is an interactive TUI).
#
# Usage:
#   ./dev.sh                  # start all daemons (mongo + collector + api + frontend)
#   ./dev.sh start            # same
#   ./dev.sh start api
#   ./dev.sh start frontend
#   ./dev.sh stop
#   ./dev.sh stop api
#   ./dev.sh restart
#   ./dev.sh status
#   ./dev.sh logs [all|api|frontend|collector|mongo]
#
# Safety:
#   - Only kills PIDs that were started by this script
#   - Verifies /proc/<pid>/cmdline still looks like one of ours
#   - Never touches containers or processes it did not create
#

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${SCRIPT_DIR}/.pids"
LOG_DIR="${SCRIPT_DIR}/logs"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Components we know how to manage as daemons
KNOWN_COMPONENTS=("mongo" "collector" "api" "frontend")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
log_info()    { echo -e "${CYAN}[INFO]${NC}    $*" >&2; }
log_ok()      { echo -e "${GREEN}[OK]${NC}      $*" >&2; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}    $*" >&2; }
log_error()   { echo -e "${RED}[ERROR]${NC}   $*" >&2; }

ensure_dirs() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
}

load_env() {
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        # shellcheck disable=SC2046
        export $(grep -v '^#' "${SCRIPT_DIR}/.env" | xargs -d '\n' -I {} echo {}) 2>/dev/null || true
    fi
}

# Return 0 if this PID looks like it was started by us
is_our_process() {
    local pid="$1"
    local component="${2:-}"

    # Must exist
    [[ -d "/proc/$pid" ]] || return 1

    local cmdline
    cmdline=$(cat "/proc/$pid/cmdline" 2>/dev/null | tr '\0' ' ' || true)

    # Must contain forza_telemetry somewhere (collector)
    if [[ "$cmdline" == *"forza_telemetry"* ]] || [[ "$cmdline" == *"forza-telemetry"* ]]; then
        return 0
    fi

    # API (Flask)
    if [[ "$cmdline" == *"flask"* && "$cmdline" == *"api.app"* ]]; then
        return 0
    fi

    # Frontend (Vite) - can be npm, node, or vite directly
    if [[ "$cmdline" == *"vite"* ]] || 
       [[ "$cmdline" == *"node"* && "$cmdline" == *"frontend"* ]] ||
       [[ "$cmdline" == *"npm"* && "$cmdline" == *"dev"* && "$cmdline" == *"3003"* ]]; then
        return 0
    fi

    # Special case for mongo component (we don't track a real PID for it)
    if [[ "$component" == "mongo" ]]; then
        return 0
    fi

    return 1
}

get_pid() {
    local component="$1"
    local pid_file="${PID_DIR}/${component}.pid"
    [[ -f "$pid_file" ]] && cat "$pid_file" || echo ""
}

write_pid() {
    local component="$1"
    local pid="$2"
    echo "$pid" > "${PID_DIR}/${component}.pid"
}

remove_pid() {
    local component="$1"
    rm -f "${PID_DIR}/${component}.pid"
}

# -----------------------------------------------------------------------------
# Component: mongo (managed via docker compose)
# -----------------------------------------------------------------------------
start_mongo() {
    log_info "Starting MongoDB via docker compose (mongo:latest)..."

    if ! command -v docker &>/dev/null; then
        log_error "docker is not installed or not in PATH"
        return 1
    fi

    if [[ ! -f "$COMPOSE_FILE" ]]; then
        log_error "docker-compose.yml not found"
        return 1
    fi

    # Start only the mongo service
    docker compose -f "$COMPOSE_FILE" up -d mongo

    # Create a marker file (not a real PID, but allows our stop logic to know we started it)
    echo "docker" > "${PID_DIR}/mongo.pid"

    # Wait for healthy
    log_info "Waiting for MongoDB to become healthy..."
    for i in {1..30}; do
        if docker compose -f "$COMPOSE_FILE" ps mongo 2>/dev/null | grep -q "(healthy)"; then
            log_ok "MongoDB is healthy"
            return 0
        fi
        sleep 1
    done

    log_warn "MongoDB container is up but not yet reporting healthy (it may still be starting)"
    return 0
}

stop_mongo() {
    local pid_file="${PID_DIR}/mongo.pid"

    if [[ ! -f "$pid_file" ]]; then
        log_warn "No record that we started mongo (nothing to stop)"
        return 0
    fi

    log_info "Stopping MongoDB container..."
    docker compose -f "$COMPOSE_FILE" stop mongo || true
    docker compose -f "$COMPOSE_FILE" rm -f mongo || true

    remove_pid "mongo"
    log_ok "MongoDB stopped"
}

# -----------------------------------------------------------------------------
# Component: collector (real Python daemon with PID tracking)
# -----------------------------------------------------------------------------
start_collector() {
    local pid_file="${PID_DIR}/collector.pid"

    # If already running and ours, do nothing
    local existing_pid
    existing_pid=$(get_pid "collector")
    if [[ -n "$existing_pid" ]] && is_our_process "$existing_pid" "collector"; then
        log_warn "Collector already running (PID $existing_pid)"
        return 0
    fi

    # Clean stale PID file
    if [[ -n "$existing_pid" ]]; then
        log_warn "Removing stale PID file for collector"
        remove_pid "collector"
    fi

    load_env

    # Find the best Python interpreter
    local python_bin
    if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
        python_bin="${SCRIPT_DIR}/.venv/bin/python"
    elif [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
        python_bin="${SCRIPT_DIR}/.venv/bin/python3"
    else
        python_bin="$(command -v python3 || command -v python)"
    fi

    if [[ -z "$python_bin" ]]; then
        log_error "No Python interpreter found. Create a venv or install python3."
        return 1
    fi

    log_info "Starting collector using: $python_bin"

    # Run in background with log rotation friendly output
    local log_file="${LOG_DIR}/collector.log"

    # Use setsid so it gets its own process group (cleaner Ctrl+C behavior for the group)
    setsid "$python_bin" -m forza_telemetry collector \
        >>"$log_file" 2>&1 < /dev/null &

    local pid=$!

    # Give it a moment to either crash or stay alive
    sleep 1.2

    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "Collector failed to stay alive. Check logs:"
        echo "    tail -n 50 $log_file" >&2
        return 1
    fi

    write_pid "collector" "$pid"
    log_ok "Collector started (PID $pid) — logs: $log_file"
}

stop_collector() {
    local pid_file="${PID_DIR}/collector.pid"
    local pid
    pid=$(get_pid "collector")

    if [[ -z "$pid" ]]; then
        log_warn "No collector PID file found"
        return 0
    fi

    if ! is_our_process "$pid" "collector"; then
        log_warn "PID $pid does not appear to be a collector we started. Refusing to kill it."
        log_warn "If you are sure, manually remove: rm -f $pid_file"
        return 1
    fi

    log_info "Stopping collector (PID $pid)..."

    # Graceful shutdown first (the collector understands SIGTERM / SIGINT)
    kill -TERM "$pid" 2>/dev/null || true

    # Wait up to 8 seconds for clean exit
    for i in {1..8}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    # Hard kill if still alive
    if kill -0 "$pid" 2>/dev/null; then
        log_warn "Collector did not exit cleanly, sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
    fi

    remove_pid "collector"
    log_ok "Collector stopped"
}

# -----------------------------------------------------------------------------
# Component: api (Flask development server on port 5003)
# -----------------------------------------------------------------------------
start_api() {
    local pid_file="${PID_DIR}/api.pid"

    local existing_pid
    existing_pid=$(get_pid "api")
    if [[ -n "$existing_pid" ]] && is_our_process "$existing_pid" "api"; then
        log_warn "API already running (PID $existing_pid)"
        return 0
    fi

    if [[ -n "$existing_pid" ]]; then
        remove_pid "api"
    fi

    load_env

    local python_bin
    if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
        python_bin="${SCRIPT_DIR}/.venv/bin/python"
    elif [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
        python_bin="${SCRIPT_DIR}/.venv/bin/python3"
    else
        python_bin="$(command -v python3 || command -v python)"
    fi

    if [[ -z "$python_bin" ]]; then
        log_error "No Python interpreter found for API."
        return 1
    fi

    log_info "Starting Flask API (dev) on port 5003 using: $python_bin"

    local log_file="${LOG_DIR}/api.log"

    # Set Flask environment
    export FLASK_APP="api.app:create_app"
    export FLASK_ENV=development
    export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

    # Use setsid for clean process group
    setsid "$python_bin" -m flask run \
        --host 0.0.0.0 \
        --port 5003 \
        >>"$log_file" 2>&1 < /dev/null &

    local pid=$!

    sleep 1.5

    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "API failed to start. Check logs:"
        echo "    tail -n 50 $log_file" >&2
        return 1
    fi

    write_pid "api" "$pid"
    log_ok "API started (PID $pid) on http://localhost:5003 — logs: $log_file"
}

stop_api() {
    local pid_file="${PID_DIR}/api.pid"
    local pid
    pid=$(get_pid "api")

    if [[ -z "$pid" ]]; then
        log_warn "No API PID file found"
        return 0
    fi

    if ! is_our_process "$pid" "api"; then
        log_warn "PID $pid does not appear to be an API we started."
        remove_pid "api"
        return 1
    fi

    log_info "Stopping API (PID $pid)..."

    kill -TERM "$pid" 2>/dev/null || true

    for i in {1..5}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
    fi

    remove_pid "api"
    log_ok "API stopped"
}

# -----------------------------------------------------------------------------
# Component: frontend (Vite dev server on port 3003)
# -----------------------------------------------------------------------------
start_frontend() {
    local pid_file="${PID_DIR}/frontend.pid"

    local existing_pid
    existing_pid=$(get_pid "frontend")
    if [[ -n "$existing_pid" ]] && is_our_process "$existing_pid" "frontend"; then
        log_warn "Frontend already running (PID $existing_pid)"
        return 0
    fi

    if [[ -n "$existing_pid" ]]; then
        remove_pid "frontend"
    fi

    local frontend_dir="${SCRIPT_DIR}/frontend"

    if [[ ! -d "$frontend_dir" ]]; then
        log_error "frontend/ directory not found"
        return 1
    fi

    if [[ ! -f "$frontend_dir/package.json" ]]; then
        log_error "frontend/package.json not found. Did you run 'npm install' in frontend/?"
        return 1
    fi

    log_info "Starting Vite frontend dev server on port 3003..."

    local log_file="${LOG_DIR}/frontend.log"

    # Run npm from within frontend directory.
    # We set VITE_API_TARGET so the Vite proxy forwards /api calls to the correct backend port.
    (
        cd "$frontend_dir" || exit 1
        VITE_API_TARGET=http://localhost:5003 npm run dev -- --port 3003 --host 0.0.0.0
    ) >>"$log_file" 2>&1 &

    local pid=$!

    sleep 2

    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "Frontend failed to start. Check logs:"
        echo "    tail -n 50 $log_file" >&2
        return 1
    fi

    write_pid "frontend" "$pid"
    log_ok "Frontend started (PID $pid) on http://localhost:3003 — logs: $log_file"
}

stop_frontend() {
    local pid_file="${PID_DIR}/frontend.pid"
    local pid
    pid=$(get_pid "frontend")

    if [[ -z "$pid" ]]; then
        log_warn "No frontend PID file found"
        return 0
    fi

    if ! is_our_process "$pid" "frontend"; then
        log_warn "PID $pid does not appear to be a frontend we started."
        remove_pid "frontend"
        return 1
    fi

    log_info "Stopping frontend (PID $pid)..."

    kill -TERM "$pid" 2>/dev/null || true

    for i in {1..5}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
    fi

    remove_pid "frontend"
    log_ok "Frontend stopped"
}

# -----------------------------------------------------------------------------
# Generic component dispatch
# -----------------------------------------------------------------------------
start_component() {
    local name="$1"
    case "$name" in
        mongo)     start_mongo ;;
        collector) start_collector ;;
        api)       start_api ;;
        frontend)  start_frontend ;;
        *)         log_error "Unknown component: $name"; return 1 ;;
    esac
}

stop_component() {
    local name="$1"
    case "$name" in
        mongo)     stop_mongo ;;
        collector) stop_collector ;;
        api)       stop_api ;;
        frontend)  stop_frontend ;;
        *)         log_error "Unknown component: $name"; return 1 ;;
    esac
}

# -----------------------------------------------------------------------------
# High level commands
# -----------------------------------------------------------------------------
start_all() {
    ensure_dirs
    log_info "Starting all development daemons..."
    start_component mongo
    start_component collector
    start_component api
    start_component frontend
    log_ok "Development stack is up (mongo + collector + api:5003 + frontend:3003)"
}

stop_all() {
    log_info "Stopping all development daemons..."
    # Stop in reverse order (frontend first, then api, etc.)
    stop_component frontend || true
    stop_component api || true
    stop_component collector || true
    stop_component mongo || true
    log_ok "Development stack stopped"
}

restart_all() {
    log_info "Restarting development stack..."
    stop_all
    sleep 1
    start_all
}

status() {
    echo
    echo -e "${BLUE}=== Forza Telemetry Dev Status ===${NC}"

    # Collector
    local cpid
    cpid=$(get_pid "collector")
    if [[ -n "$cpid" ]] && is_our_process "$cpid" "collector"; then
        echo -e "  collector   ${GREEN}running${NC}   (PID $cpid)"
    else
        echo -e "  collector   ${RED}stopped${NC}"
        [[ -f "${PID_DIR}/collector.pid" ]] && echo "               (stale PID file present)"
    fi

    # Mongo
    if docker compose -f "$COMPOSE_FILE" ps mongo 2>/dev/null | grep -q "Up"; then
        local healthy
        healthy=$(docker compose -f "$COMPOSE_FILE" ps mongo 2>/dev/null | grep -o "(healthy)\|(unhealthy)" || true)
        if [[ "$healthy" == *"(healthy)"* ]]; then
            echo -e "  mongo       ${GREEN}running${NC}   (docker, healthy)"
        else
            echo -e "  mongo       ${YELLOW}running${NC}   (docker, starting...)"
        fi
    else
        echo -e "  mongo       ${RED}stopped${NC}"
    fi

    # API (Flask on 5003)
    local apid
    apid=$(get_pid "api")
    if [[ -n "$apid" ]] && is_our_process "$apid" "api"; then
        echo -e "  api         ${GREEN}running${NC}   (PID $apid)  → http://localhost:5003"
    else
        echo -e "  api         ${RED}stopped${NC}"
        [[ -f "${PID_DIR}/api.pid" ]] && echo "               (stale PID file present)"
    fi

    # Frontend (Vite on 3003)
    local fpid
    fpid=$(get_pid "frontend")
    if [[ -n "$fpid" ]] && is_our_process "$fpid" "frontend"; then
        echo -e "  frontend    ${GREEN}running${NC}   (PID $fpid)  → http://localhost:3003"
    else
        echo -e "  frontend    ${RED}stopped${NC}"
        [[ -f "${PID_DIR}/frontend.pid" ]] && echo "               (stale PID file present)"
    fi

    echo
    echo -e "  Logs:      ${LOG_DIR}/"
    echo -e "  PIDs:      ${PID_DIR}/"
    echo
}

logs() {
    local components=("$@")

    # Default to collector if no args
    if [[ ${#components[@]} -eq 0 ]]; then
        components=("collector")
    fi

    # Special case: logs all
    if [[ "${components[0]}" == "all" ]]; then
        log_info "Tailing all available logs (Ctrl+C to stop)..."
        if ls "${LOG_DIR}"/*.log &>/dev/null; then
            tail -f "${LOG_DIR}"/*.log
        else
            log_warn "No log files found in ${LOG_DIR}/ yet."
        fi
        return
    fi

    # Handle mongo specially (docker logs)
    if [[ " ${components[*]} " == *" mongo "* ]]; then
        log_info "Tailing docker logs for mongo..."
        docker compose -f "$COMPOSE_FILE" logs -f mongo &
        # Remove mongo so we don't try to tail its file
        components=("${components[@]/mongo}")
    fi

    # Collect real log files for other components
    local log_files=()
    for comp in "${components[@]}"; do
        [[ -z "$comp" ]] && continue

        local lf="${LOG_DIR}/${comp}.log"

        # Special mapping
        if [[ "$comp" == "frontend" ]]; then
            lf="${LOG_DIR}/frontend.log"
        fi

        if [[ -f "$lf" ]]; then
            log_files+=("$lf")
        else
            log_warn "No log file for component '$comp' yet: $lf"
        fi
    done

    if [[ ${#log_files[@]} -eq 0 ]]; then
        log_error "No valid log files to tail."
        return 1
    fi

    log_info "Tailing: ${log_files[*]} (Ctrl+C to stop)..."
    tail -f "${log_files[@]}"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [COMMAND] [COMPONENT...]

Commands:
  start [component]       Start all (or specific) daemons
  stop  [component]       Stop all (or specific) daemons safely
  restart [component]     Restart all (or specific)
  status                  Show status of all managed components
  logs [component|all]    Tail logs

Components:
  mongo, collector, api, frontend

Logs examples:
  ./dev.sh logs                    # default: collector
  ./dev.sh logs all                # tail all components at once
  ./dev.sh logs api frontend       # tail specific components
  ./dev.sh logs collector

Other examples:
  ./dev.sh
  ./dev.sh start
  ./dev.sh start api frontend
  ./dev.sh status
EOF
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    ensure_dirs

    local cmd="${1:-start}"
    local component="${2:-}"

    case "$cmd" in
        start)
            if [[ -n "$component" ]]; then
                start_component "$component"
            else
                start_all
            fi
            ;;
        stop)
            if [[ -n "$component" ]]; then
                stop_component "$component"
            else
                stop_all
            fi
            ;;
        restart)
            if [[ -n "$component" ]]; then
                stop_component "$component" || true
                sleep 0.8
                start_component "$component"
            else
                restart_all
            fi
            ;;
        status)
            status
            ;;
        logs)
            shift  # remove "logs"
            logs "$@"
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            log_error "Unknown command: $cmd"
            usage
            exit 1
            ;;
    esac
}

main "$@"
