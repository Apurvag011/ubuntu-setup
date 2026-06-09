#!/bin/bash
# setup.sh — Bootstrap installer: Ansible → Docker → Docker Compose
#
# Run as a sudo-capable user (NOT as root):
#   chmod +x setup.sh && ./setup.sh
#
# Expected layout (copy everything to your home folder):
#   ~/ansible_install/
#   ~/docker-install/
#   ~/docker_compose_install/
#   ~/setup.sh  (this file)

set -uo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="$SCRIPT_DIR/ansible_install"
DOCKER_DIR="$SCRIPT_DIR/docker-install"
COMPOSE_DIR="$SCRIPT_DIR/docker_compose_install"
LOG_FILE="$SCRIPT_DIR/setup.log"

# ---------------------------------------------------------------------------
# Colours (disabled when not a TTY)
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()         { echo -e "${BOLD}[$(date '+%H:%M:%S')]${RESET} $*" | tee -a "$LOG_FILE"; }
log_step()    { echo -e "\n${CYAN}${BOLD}══════════════════════════════════════${RESET}" | tee -a "$LOG_FILE"
                echo -e "${CYAN}${BOLD}  $*${RESET}" | tee -a "$LOG_FILE"
                echo -e "${CYAN}${BOLD}══════════════════════════════════════${RESET}" | tee -a "$LOG_FILE"; }
log_ok()      { echo -e "${GREEN}${BOLD}  ✔ $*${RESET}" | tee -a "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}${BOLD}  ⚠ $*${RESET}" | tee -a "$LOG_FILE"; }
log_fail()    { echo -e "${RED}${BOLD}  ✘ $*${RESET}" | tee -a "$LOG_FILE"; }

# Run a command, stream its output to the console AND the log file.
# Returns the exit code of the command.
run() {
    "$@" 2>&1 | tee -a "$LOG_FILE"
    return "${PIPESTATUS[0]}"
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight() {
    log_step "Pre-flight checks"

    # Must NOT be root
    if [ "$(id -u)" -eq 0 ]; then
        log_fail "Do not run this script as root. Run as a sudo-capable user: ./setup.sh"
        exit 1
    fi

    # sudo must be available and working
    if ! sudo -v 2>/dev/null; then
        log_fail "sudo is not available or your password was rejected."
        exit 1
    fi

    # Ubuntu 22.04 Jammy check
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        if [ "${VERSION_CODENAME:-}" != "jammy" ]; then
            log_fail "Unsupported OS: $PRETTY_NAME. This setup requires Ubuntu 22.04 (Jammy)."
            exit 1
        fi
        log_ok "OS: $PRETTY_NAME"
    else
        log_fail "/etc/os-release not found. Cannot verify OS."
        exit 1
    fi

    # Architecture check
    ARCH="$(uname -m)"
    if [ "$ARCH" != "x86_64" ]; then
        log_fail "Unsupported architecture: $ARCH. Requires x86_64."
        exit 1
    fi
    log_ok "Architecture: $ARCH"

    # Required directories
    for dir in "$ANSIBLE_DIR" "$DOCKER_DIR" "$COMPOSE_DIR"; do
        if [ ! -d "$dir" ]; then
            log_fail "Missing directory: $dir"
            log_fail "Copy all installer folders next to setup.sh before running."
            exit 1
        fi
    done
    log_ok "All installer directories present"

    # Required files
    local ansible_script="$ANSIBLE_DIR/install_ansible.py"
    local ansible_tarball="$ANSIBLE_DIR/ansible-offline-jammy-x86_64_packages.tar.gz"
    local docker_playbook="$DOCKER_DIR/site.yml"
    local compose_script="$COMPOSE_DIR/install_compose_plugin.sh"
    local compose_binary="$COMPOSE_DIR/docker-compose"

    for f in "$ansible_script" "$ansible_tarball" "$docker_playbook" \
              "$compose_script" "$compose_binary"; do
        if [ ! -f "$f" ]; then
            log_fail "Missing file: $f"
            exit 1
        fi
    done
    log_ok "All required files present"

    log_ok "Pre-flight passed"
}

# ---------------------------------------------------------------------------
# Step 1 — Install Ansible
# ---------------------------------------------------------------------------
install_ansible() {
    log_step "Step 1/3 — Installing Ansible (offline)"

    if command -v ansible &>/dev/null; then
        log_ok "Ansible already installed: $(ansible --version | head -1)"
        return 0
    fi

    # The installer looks for the tarball in its own directory
    if ! (cd "$ANSIBLE_DIR" && run sudo python3 install_ansible.py); then
        log_fail "Ansible installation FAILED."
        log_fail "Full log: $LOG_FILE"
        exit 1
    fi

    # Verify
    if ! command -v ansible &>/dev/null; then
        log_fail "Ansible binary not found after install. Check $LOG_FILE for details."
        exit 1
    fi

    log_ok "Ansible installed: $(ansible --version | head -1)"
}

# ---------------------------------------------------------------------------
# Step 2 — Install Docker (via Ansible)
# ---------------------------------------------------------------------------
install_docker() {
    log_step "Step 2/3 — Installing Docker (Ansible playbook)"

    if command -v docker &>/dev/null && docker version &>/dev/null 2>&1; then
        log_ok "Docker already installed: $(docker version --format '{{.Server.Version}}' 2>/dev/null || docker --version)"
        return 0
    fi

    # Install required Ansible collections for the docker role
    log "Installing required Ansible collections..."
    if ! run ansible-galaxy collection install community.general ansible.posix; then
        log_fail "Failed to install Ansible collections."
        log_warn "If offline, install collections manually from a tarball."
        exit 1
    fi
    log_ok "Ansible collections ready"

    # Run the playbook
    if ! (cd "$DOCKER_DIR" && run ansible-playbook site.yml); then
        log_fail "Docker installation playbook FAILED."
        log_fail "Full log: $LOG_FILE"
        log_warn "Troubleshooting tips:"
        log_warn "  journalctl -xeu containerd.service --no-pager | tail -40"
        log_warn "  journalctl -xeu docker.service --no-pager | tail -40"
        exit 1
    fi

    # Verify the Docker socket is up
    if ! sudo docker version &>/dev/null; then
        log_fail "Docker socket is not responding after install. Check $LOG_FILE"
        exit 1
    fi

    log_ok "Docker installed: $(sudo docker version --format '{{.Server.Version}}')"
    log_warn "Note: log out and back in (or run 'newgrp docker') for your user to use Docker without sudo."
}

# ---------------------------------------------------------------------------
# Step 3 — Install Docker Compose plugin
# ---------------------------------------------------------------------------
install_docker_compose() {
    log_step "Step 3/3 — Installing Docker Compose plugin"

    if docker compose version &>/dev/null 2>&1; then
        log_ok "Docker Compose already installed: $(docker compose version)"
        return 0
    fi

    chmod +x "$COMPOSE_DIR/install_compose_plugin.sh"

    if ! (cd "$COMPOSE_DIR" && run ./install_compose_plugin.sh); then
        log_fail "Docker Compose installation FAILED."
        log_fail "Full log: $LOG_FILE"
        exit 1
    fi

    if ! docker compose version &>/dev/null 2>&1; then
        log_fail "'docker compose' command not working after install. Check $LOG_FILE"
        exit 1
    fi

    log_ok "Docker Compose installed: $(docker compose version)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Truncate log for this run
    : > "$LOG_FILE"
    log "Setup started at $(date)"
    log "Log file: $LOG_FILE"

    preflight
    install_ansible
    install_docker
    install_docker_compose

    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${RESET}"
    echo -e "${GREEN}${BOLD}║   ALL STEPS COMPLETED SUCCESSFULLY   ║${RESET}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${RESET}"
    echo ""
    log_ok "Ansible:         $(ansible --version | head -1)"
    log_ok "Docker:          $(sudo docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'installed')"
    log_ok "Docker Compose:  $(docker compose version 2>/dev/null || echo 'installed')"
    echo ""
    log_warn "Action required: run 'newgrp docker' or log out/in so your user can run Docker without sudo."
    echo ""
    log "Full log saved to: $LOG_FILE"
}

main "$@"
