# docker_install — Ansible Role

Installs Docker Engine from a local static binary tarball. Designed for air-gapped environments with no internet access.

## Requirements

- Target OS: **Ubuntu 22.04 LTS (Jammy)** — the role will hard-fail on anything else
- Ansible: >= 2.12
- Collections: `community.general`, `ansible.posix`

## Install required collections

```bash
ansible-galaxy collection install community.general ansible.posix
```

## Directory structure

```
docker-install/
├── ansible.cfg
├── site.yml
├── inventory/
│   └── hosts.ini
├── files/
│   └── docker-29.5.3.tgz     # Bundled tarball — ready to use out of the box
└── roles/
    └── docker_install/
        ├── defaults/main.yml     # All tunable variables
        ├── handlers/main.yml
        ├── meta/main.yml
        └── tasks/
            ├── main.yml          # Orchestrator — imports the others in order
            ├── preflight.yml     # OS check, tarball presence, binary inventory
            ├── kernel.yml        # Modules (overlay, br_netfilter) + sysctl
            ├── install.yml       # Binaries, dirs, group, daemon.json
            ├── systemd.yml       # Unit files for containerd + docker
            └── validate.yml      # Start services, check socket, docker version
```

## Variables (defaults/main.yml)

| Variable | Default | Description |
|---|---|---|
| `docker_tar_src` | `files/docker-29.5.3.tgz` | Path to the tarball (bundled in this project) |
| `docker_extract_dir` | `/tmp/docker-install` | Temporary extraction directory |
| `docker_bin_dir` | `/usr/local/bin` | Where binaries are installed |
| `docker_data_dir` | `/var/lib/docker` | Docker data root |
| `docker_storage_driver` | `overlay2` | Storage driver for daemon.json |

## Usage

The tarball is already bundled in `files/`. Copy this project to the target machine and run:

**1. Copy the project to the target host:**
```bash
scp -r docker-install/ your_user@<host>:~/docker-install
```

**2. Run the playbook:**
```bash
cd docker-install
ansible-playbook site.yml
```

The inventory defaults to `localhost` with your current shell user — no edits needed for local installs.

**3. After the play completes, log out and back in** (or run `newgrp docker`) so your user's `docker` group membership takes effect.

**To target a remote host instead of localhost**, edit `inventory/hosts.ini`:
```ini
192.168.1.100 ansible_user=ec2-user ansible_ssh_private_key_file=~/.ssh/id_rsa
```

**To use a different tarball:**
```bash
ansible-playbook site.yml -e "docker_tar_src=/opt/packages/docker-custom.tgz"
```

## Task execution order

```
preflight   →  OS assert, tarball stat, unarchive, binary inventory
kernel      →  modprobe overlay + br_netfilter, sysctl, confirm loaded
install     →  copy binaries, mkdir, create group, add user, daemon.json
systemd     →  write containerd/docker.socket/docker unit files
validate    →  start services, check socket, docker version, docker info
```

Every task has a `failed_when` or `assert` with a descriptive message so you know exactly what failed and why.

## Troubleshooting

```bash
# If containerd fails to start
journalctl -xeu containerd.service --no-pager | tail -40

# If docker fails to start
journalctl -xeu docker.service --no-pager | tail -40

# Confirm modules are loaded
lsmod | grep -E 'overlay|br_netfilter'

# Confirm sysctl values
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables
```
