# Ansible Offline Installer — Ubuntu 22.04 Jammy

Installs Ansible from a local `.tar.gz` bundle of `.deb` packages with no internet access required. Idempotent: exits cleanly if Ansible is already installed.

## Requirements

| Requirement | Detail |
|---|---|
| OS | Ubuntu 22.04 LTS (Jammy Jellyfish) |
| Architecture | x86_64 |
| Python | 3.10+ (ships with Ubuntu 22.04) |
| Privileges | Must be run as root (`sudo`) |

## Files

| File | Description |
|---|---|
| `install_ansible.py` | Main installer script |
| `ansible-offline-jammy-x86_64_packages.tar.gz` | Offline `.deb` package bundle |

## Usage

```bash
# Standard install (tarball must be in the same directory)
sudo python3 install_ansible.py

# Specify a custom tarball path
sudo python3 install_ansible.py --tarball /path/to/ansible-offline-jammy-x86_64_packages.tar.gz

# Validate and plan without making any changes
sudo python3 install_ansible.py --dry-run
```

## What It Does

The installer runs 7 steps and prints a summary at the end:

1. **Pre-check** — Exits cleanly if Ansible is already installed (idempotent).
2. **Environment validation** — Confirms Ubuntu 22.04 Jammy and x86_64 architecture.
3. **Tarball validation** — Verifies the archive exists and contains `.deb` files.
4. **Extraction** — Unpacks the archive to a temporary directory under `/tmp`.
5. **Normal install** — Runs `dpkg -i` on all standard packages (two-pass to handle ordering).
6. **Force-depends install** — Runs `dpkg -i --force-depends` on Perl SSL packages that depend on the `perl-openssl-abi-3` virtual package (provided by `libssl3`).
7. **Verification** — Runs `ansible --version` and `ansible localhost -m ping` to confirm the install is healthy.

Temporary files are always cleaned up, even on failure.

## Force-Depends Packages

These four Perl packages are installed with `--force-depends` because `dpkg` cannot statically resolve the `perl-openssl-abi-3` virtual package (provided by `libssl3`). The flag is safe here because `libssl3` is present on Ubuntu 22.04 by default.

- `libnet-ssleay-perl`
- `libio-socket-ssl-perl`
- `liblwp-protocol-https-perl`
- `libwww-perl`

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All steps succeeded (or Ansible already installed) |
| `1` | One or more steps failed |

## Output

The script uses colored, timestamped output and prints a final summary table:

```
════════════════════════════════════════════════════════════════════════
  INSTALLATION SUMMARY
════════════════════════════════════════════════════════════════════════
  ● skip   pre-existing ansible check  →  not installed, will install
  ✔ ok     OS check  →  Ubuntu 22.04 (jammy)
  ✔ ok     architecture check  →  x86_64
  ...
────────────────────────────────────────────────────────────────────────
  Overall: SUCCESS   elapsed: 12.3s
════════════════════════════════════════════════════════════════════════
```

Colors are disabled automatically when output is not a terminal (e.g., redirected to a log file).

## Building the Tarball

The tarball must contain `.deb` files for all Ansible dependencies targeting Ubuntu 22.04 Jammy x86_64. To build it on a machine with internet access:

```bash
mkdir ansible-debs && cd ansible-debs
apt-get download ansible $(apt-cache depends --recurse --no-recommends \
  --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances \
  ansible | grep "^\w" | sort -u)
cd .. && tar czf ansible-offline-jammy-x86_64_packages.tar.gz ansible-debs/
```
