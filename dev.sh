#!/usr/bin/env bash
#
# dev.sh - Forza Horizon 6 Telemetry Development Environment Manager
#
# Starts, stops, and restarts the local development stack safely.
#
# Components managed:
#   - mongo     → MongoDB via docker compose (latest image)
#   - collector → The UDP telemetry collector (Python daemon)
#
# The dashboard is intentionally NOT managed here (it is an interactive TUI).
#
# Usage:
#   ./dev.sh                  # start all daemons
#   ./dev.sh start            # same
#   ./dev.sh start collector
#   ./dev.sh stop
#   ./dev.sh stop mongo
#   ./dev.sh restart
#   ./dev.sh restart collector
#   ./dev.sh status
#   ./dev.sh logs [collector|mongo]
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
KNOWN_COMPONENTS=("mongo" "collector")

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
# Generic component dispatch
# -----------------------------------------------------------------------------
start_component() {
    local name="$1"
    case "$name" in
        mongo)     start_mongo ;;
        collector) start_collector ;;
        *)         log_error "Unknown component: $name"; return 1 ;;
    esac
}

stop_component() {
    local name="$1"
    case "$name" in
        mongo)     stop_mongo ;;
        collector) stop_collector ;;
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
    log_ok "Development stack is up"
}

stop_all() {
    log_info "Stopping all development daemons..."
    # Stop in reverse order
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

    echo
    echo -e "  Logs:      ${LOG_DIR}/"
    echo -e "  PIDs:      ${PID_DIR}/"
    echo
}

logs() {
    local component="${1:-collector}"
    local log_file="${LOG_DIR}/${component}.log"

    if [[ "$component" == "mongo" ]]; then
        log_info "Tailing docker logs for mongo..."
        docker compose -f "$COMPOSE_FILE" logs -f mongo
        return
    fi

    if [[ ! -f "$log_file" ]]; then
        log_error "No log file for $component yet: $log_file"
        return 1
    fi

    log_info "Tailing $log_file (Ctrl+C to stop)..."
    tail -f "$log_file"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [COMMAND] [COMPONENT]

Commands:
  start [component]     Start all (or one) daemons
  stop  [component]     Stop all (or one) daemons safely
  restart [component]   Restart all (or one)
  status                Show status of managed components
  logs [component]      Tail logs (collector or mongo)

Components:
  collector, mongo

Examples:
  ./dev.sh
  ./dev.sh start
  ./dev.sh start collector
  ./dev.sh restart
  ./dev.sh stop mongo
  ./dev.sh logs
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
            logs "$component"
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
