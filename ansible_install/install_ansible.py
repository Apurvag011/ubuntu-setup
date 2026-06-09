#!/usr/bin/env python3
"""
Ansible Offline Installer for Ubuntu 22.04 Jammy
=================================================
Installs Ansible from a local .tar.gz bundle of .deb packages.
Idempotent: exits cleanly if Ansible is already installed.

Usage:
    sudo python3 install_ansible_offline.py
    sudo python3 install_ansible_offline.py --tarball /path/to/custom.tar.gz
    sudo python3 install_ansible_offline.py --dry-run
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TARBALL = "ansible-offline-jammy-x86_64_packages.tar.gz"

# Packages that require --force-depends due to virtual package dependencies.
# perl-openssl-abi-3 is a virtual provided by libssl3 but dpkg can't resolve it
# statically; force-depends bypasses that check safely since libssl3 is present.
FORCE_DEPENDS_PACKAGES = [
    "libnet-ssleay-perl",
    "libio-socket-ssl-perl",
    "liblwp-protocol-https-perl",
    "libwww-perl",
]

# Minimum ansible version considered acceptable (tuple comparison)
MIN_ANSIBLE_VERSION = (2, 10)


# ---------------------------------------------------------------------------
# ANSI colours (disabled automatically when not a TTY)
# ---------------------------------------------------------------------------

class Colour:
    _enabled = sys.stdout.isatty()

    GREEN  = "\033[92m" if _enabled else ""
    YELLOW = "\033[93m" if _enabled else ""
    RED    = "\033[91m" if _enabled else ""
    CYAN   = "\033[96m" if _enabled else ""
    BOLD   = "\033[1m"  if _enabled else ""
    RESET  = "\033[0m"  if _enabled else ""


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")

def log_info(msg: str) -> None:
    print(f"{Colour.CYAN}[{_ts()}] INFO   {Colour.RESET} {msg}")

def log_ok(msg: str) -> None:
    print(f"{Colour.GREEN}[{_ts()}] OK     {Colour.RESET} {msg}")

def log_warn(msg: str) -> None:
    print(f"{Colour.YELLOW}[{_ts()}] WARN   {Colour.RESET} {msg}")

def log_error(msg: str) -> None:
    print(f"{Colour.RED}[{_ts()}] ERROR  {Colour.RESET} {msg}", file=sys.stderr)

def log_step(n: int, total: int, msg: str) -> None:
    print(f"\n{Colour.BOLD}[{_ts()}] Step {n}/{total}: {msg}{Colour.RESET}")


# ---------------------------------------------------------------------------
# Summary tracking
# ---------------------------------------------------------------------------

class Summary:
    """Accumulates every step result and prints a final report."""

    def __init__(self) -> None:
        self.started_at: float = time.time()
        self.steps: list[dict] = []
        self.warnings: list[str] = []

    def record(self, step: str, status: str, detail: str = "") -> None:
        """status: 'ok' | 'skip' | 'warn' | 'fail'"""
        self.steps.append({
            "step": step,
            "status": status,
            "detail": detail,
        })

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        log_warn(msg)

    def print_report(self) -> None:
        elapsed = time.time() - self.started_at
        width = 72

        status_icon = {
            "ok":   f"{Colour.GREEN}✔ ok    {Colour.RESET}",
            "skip": f"{Colour.CYAN}● skip  {Colour.RESET}",
            "warn": f"{Colour.YELLOW}⚠ warn  {Colour.RESET}",
            "fail": f"{Colour.RED}✘ fail  {Colour.RESET}",
        }

        print("\n" + "═" * width)
        print(f"{Colour.BOLD}  INSTALLATION SUMMARY{Colour.RESET}")
        print("═" * width)

        for s in self.steps:
            icon = status_icon.get(s["status"], "? ")
            line = f"  {icon} {s['step']}"
            if s["detail"]:
                line += f"  →  {s['detail']}"
            print(line)

        if self.warnings:
            print("\n  " + "─" * (width - 2))
            print(f"  {Colour.YELLOW}Warnings:{Colour.RESET}")
            for w in self.warnings:
                print(f"    • {w}")

        overall = (
            "FAILED"
            if any(s["status"] == "fail" for s in self.steps)
            else "SUCCESS"
        )
        colour = Colour.GREEN if overall == "SUCCESS" else Colour.RED

        print("─" * width)
        print(
            f"  Overall: {colour}{Colour.BOLD}{overall}{Colour.RESET}"
            f"   elapsed: {elapsed:.1f}s"
        )
        print("═" * width + "\n")


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def run(
    cmd: list[str],
    capture: bool = False,
    check: bool = True,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with consistent error handling."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        env=env,
    )


def require_root() -> None:
    if os.geteuid() != 0:
        log_error("This script must be run as root (use sudo).")
        sys.exit(1)


def check_ubuntu_jammy() -> tuple[bool, str]:
    """Returns (is_jammy, description_string)."""
    try:
        result = run(["lsb_release", "-rs"], capture=True, check=False)
        release = result.stdout.strip()
        codename_result = run(["lsb_release", "-cs"], capture=True, check=False)
        codename = codename_result.stdout.strip()
        desc = f"Ubuntu {release} ({codename})"
        return codename.lower() == "jammy", desc
    except FileNotFoundError:
        return False, "unknown (lsb_release not found)"


def check_architecture() -> str:
    result = run(["uname", "-m"], capture=True)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Step 1: Check if Ansible is already installed
# ---------------------------------------------------------------------------

def check_ansible_installed() -> tuple[bool, str]:
    """Returns (installed: bool, version_string: str)."""
    try:
        result = run(["ansible", "--version"], capture=True, check=False)
        if result.returncode == 0:
            first_line = result.stdout.splitlines()[0]
            return True, first_line
        return False, ""
    except FileNotFoundError:
        return False, ""


def parse_ansible_version(version_line: str) -> tuple[int, ...]:
    """Parse 'ansible 2.10.7' or 'ansible [core 2.12.0]' into a tuple."""
    import re
    match = re.search(r"(\d+)\.(\d+)\.?(\d*)", version_line)
    if match:
        return tuple(int(x) for x in match.groups() if x)
    return (0,)


# ---------------------------------------------------------------------------
# Step 2: Validate the tarball
# ---------------------------------------------------------------------------

def validate_tarball(path: str) -> tuple[bool, str]:
    """Check the tarball exists and contains .deb files."""
    if not os.path.isfile(path):
        return False, f"File not found: {path}"
    if not tarfile.is_tarfile(path):
        return False, f"Not a valid tar archive: {path}"
    with tarfile.open(path, "r:gz") as tf:
        debs = [m.name for m in tf.getmembers() if m.name.endswith(".deb")]
    if not debs:
        return False, "Archive contains no .deb files"
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return True, f"{len(debs)} .deb files found, {size_mb:.1f} MB"


# ---------------------------------------------------------------------------
# Step 3: Extract tarball
# ---------------------------------------------------------------------------

def extract_tarball(tarball_path: str, dest_dir: str) -> tuple[bool, str, list[str]]:
    """
    Extract the tarball into dest_dir.
    Returns (success, message, list_of_deb_paths).
    """
    try:
        with tarfile.open(tarball_path, "r:gz") as tf:
            tf.extractall(path=dest_dir)

        # Find all .deb files recursively under dest_dir
        deb_files: list[str] = []
        for root, _, files in os.walk(dest_dir):
            for f in files:
                if f.endswith(".deb"):
                    deb_files.append(os.path.join(root, f))

        deb_files.sort()
        return True, f"Extracted {len(deb_files)} .deb files to {dest_dir}", deb_files
    except Exception as exc:
        return False, f"Extraction failed: {exc}", []


# ---------------------------------------------------------------------------
# Step 4: Categorise packages
# ---------------------------------------------------------------------------

def categorise_debs(
    deb_files: list[str],
) -> tuple[list[str], list[str]]:
    """
    Split .deb files into two lists:
      - normal_debs  : installed with plain dpkg -i
      - force_debs   : installed with dpkg -i --force-depends
    """
    normal: list[str] = []
    forced: list[str] = []

    for path in deb_files:
        basename = os.path.basename(path)
        is_force = any(pkg in basename for pkg in FORCE_DEPENDS_PACKAGES)
        if is_force:
            forced.append(path)
        else:
            normal.append(path)

    return normal, forced


# ---------------------------------------------------------------------------
# Step 5: Install packages
# ---------------------------------------------------------------------------

def dpkg_install(
    deb_files: list[str],
    force_depends: bool = False,
    dry_run: bool = False,
) -> tuple[bool, int, int, list[str]]:
    """
    Install a list of .deb files.
    Returns (overall_success, installed_count, error_count, error_messages).
    Two-pass install to handle ordering issues.
    """
    if not deb_files:
        return True, 0, 0, []

    cmd_base = ["dpkg", "-i"]
    if force_depends:
        cmd_base += ["--force-depends"]

    errors: list[str] = []
    installed = 0

    for attempt in (1, 2):
        if dry_run:
            log_info(f"  [dry-run] would run: {' '.join(cmd_base)} <{len(deb_files)} debs>")
            return True, len(deb_files), 0, []

        result = subprocess.run(
            cmd_base + deb_files,
            capture_output=True,
            text=True,
        )

        # Count lines that indicate successful setup
        installed = sum(
            1 for line in result.stdout.splitlines()
            if line.startswith("Setting up")
        )

        error_lines = [
            line for line in result.stderr.splitlines()
            if "error processing" in line.lower()
        ]

        if result.returncode == 0 or not error_lines:
            return True, installed, 0, []

        if attempt == 1:
            log_warn(f"  Pass 1 had {len(error_lines)} error(s); retrying (pass 2)...")
        else:
            errors = error_lines

    return len(errors) == 0, installed, len(errors), errors


# ---------------------------------------------------------------------------
# Step 6: Post-install configuration
# ---------------------------------------------------------------------------

def dpkg_configure_all(dry_run: bool = False) -> tuple[bool, str]:
    """Run dpkg --configure -a to settle any pending configurations."""
    if dry_run:
        log_info("  [dry-run] would run: dpkg --configure -a")
        return True, "skipped (dry-run)"
    result = subprocess.run(
        ["dpkg", "--configure", "-a"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, "all packages configured"


# ---------------------------------------------------------------------------
# Step 7: Verify installation
# ---------------------------------------------------------------------------

def verify_ansible(summary: Summary) -> bool:
    """Check ansible --version and ansible localhost -m ping."""
    installed, version_str = check_ansible_installed()
    if not installed:
        summary.record("ansible binary check", "fail", "ansible not found in PATH")
        return False

    version_tuple = parse_ansible_version(version_str)
    if version_tuple < MIN_ANSIBLE_VERSION:
        summary.warn(
            f"Ansible version {version_str!r} is below recommended "
            f"{'.'.join(str(x) for x in MIN_ANSIBLE_VERSION)}"
        )
        summary.record("ansible version check", "warn", version_str)
    else:
        summary.record("ansible version check", "ok", version_str)

    # Quick connectivity self-test
    ping = subprocess.run(
        ["ansible", "localhost", "-m", "ping", "--connection=local"],
        capture_output=True,
        text=True,
    )
    if ping.returncode == 0 and "SUCCESS" in ping.stdout:
        summary.record("ansible localhost ping", "ok", "pong received")
        return True
    else:
        summary.warn("ansible ping test did not return SUCCESS — may still be functional")
        summary.record("ansible localhost ping", "warn", ping.stderr.strip()[:120] or "unexpected output")
        return True  # non-fatal; binary is present


def verify_perl_chain(summary: Summary) -> None:
    """Confirm the 4 perl packages are in 'ii' (installed+configured) state."""
    for pkg in FORCE_DEPENDS_PACKAGES:
        result = subprocess.run(
            ["dpkg", "-l", pkg],
            capture_output=True,
            text=True,
        )
        lines = [l for l in result.stdout.splitlines() if pkg in l]
        if lines and lines[0].startswith("ii"):
            summary.record(f"perl pkg: {pkg}", "ok", "ii — installed and configured")
        else:
            state = lines[0][:2] if lines else "??"
            summary.warn(f"{pkg} state is '{state}', expected 'ii'")
            summary.record(f"perl pkg: {pkg}", "warn", f"state={state}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Ansible offline from a .tar.gz bundle of .deb packages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              sudo python3 install_ansible_offline.py
              sudo python3 install_ansible_offline.py --tarball /opt/ansible-offline-jammy-x86_64_packages.tar.gz
              sudo python3 install_ansible_offline.py --dry-run
        """),
    )
    parser.add_argument(
        "--tarball",
        default=DEFAULT_TARBALL,
        help=f"Path to the .tar.gz package bundle (default: {DEFAULT_TARBALL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan without making any changes to the system",
    )
    args = parser.parse_args()

    summary = Summary()
    TOTAL_STEPS = 7

    print(
        f"\n{Colour.BOLD}{'═' * 72}\n"
        f"  Ansible Offline Installer — Ubuntu 22.04 Jammy\n"
        f"{'═' * 72}{Colour.RESET}\n"
    )

    if args.dry_run:
        log_warn("DRY-RUN mode — no packages will be installed")

    # ── Privilege check ────────────────────────────────────────────────────
    require_root()

    # ── Step 1: Already installed? ─────────────────────────────────────────
    log_step(1, TOTAL_STEPS, "Checking if Ansible is already installed")
    installed, version_str = check_ansible_installed()
    if installed:
        log_ok(f"Ansible is already installed: {version_str}")
        summary.record("pre-existing ansible check", "skip", version_str)
        summary.record("installation", "skip", "nothing to do — idempotent exit")
        # Still verify the install is healthy
        log_step(2, TOTAL_STEPS, "Verifying existing installation health")
        verify_ansible(summary)
        verify_perl_chain(summary)
        summary.print_report()
        sys.exit(0)

    log_info("Ansible not found — proceeding with installation")
    summary.record("pre-existing ansible check", "ok", "not installed, will install")

    # ── Step 2: Environment checks ─────────────────────────────────────────
    log_step(2, TOTAL_STEPS, "Validating environment")

    is_jammy, os_desc = check_ubuntu_jammy()
    if not is_jammy:
        summary.warn(
            f"OS detected as '{os_desc}' — this installer targets Ubuntu 22.04 Jammy. "
            "Proceeding anyway but results may vary."
        )
        summary.record("OS check", "warn", os_desc)
    else:
        log_ok(f"OS check passed: {os_desc}")
        summary.record("OS check", "ok", os_desc)

    arch = check_architecture()
    log_ok(f"Architecture: {arch}")
    summary.record("architecture check", "ok", arch)
    if "x86_64" not in arch:
        summary.warn(
            f"Tarball is named for x86_64 but this machine reports '{arch}'. "
            "Package installation may fail."
        )

    # ── Step 3: Validate tarball ───────────────────────────────────────────
    log_step(3, TOTAL_STEPS, f"Validating tarball: {args.tarball}")

    tarball_path = os.path.abspath(args.tarball)
    valid, detail = validate_tarball(tarball_path)
    if not valid:
        log_error(detail)
        summary.record("tarball validation", "fail", detail)
        summary.print_report()
        sys.exit(1)

    log_ok(f"Tarball OK — {detail}")
    summary.record("tarball validation", "ok", detail)

    # ── Step 4: Extract ────────────────────────────────────────────────────
    log_step(4, TOTAL_STEPS, "Extracting tarball to /tmp")

    work_dir = tempfile.mkdtemp(prefix="ansible_install_", dir="/tmp")
    log_info(f"Working directory: {work_dir}")

    try:
        ok, msg, deb_files = extract_tarball(tarball_path, work_dir)
        if not ok:
            log_error(msg)
            summary.record("tarball extraction", "fail", msg)
            summary.print_report()
            sys.exit(1)

        log_ok(msg)
        summary.record("tarball extraction", "ok", msg)

        normal_debs, force_debs = categorise_debs(deb_files)
        log_info(
            f"Categorised: {len(normal_debs)} normal install, "
            f"{len(force_debs)} force-depends install"
        )

        # ── Step 5: Normal install ─────────────────────────────────────────
        log_step(5, TOTAL_STEPS, f"Installing {len(normal_debs)} packages (dpkg -i)")

        ok, installed_count, err_count, err_msgs = dpkg_install(
            normal_debs, force_depends=False, dry_run=args.dry_run
        )
        if ok:
            log_ok(f"Normal install complete — {installed_count} packages configured")
            summary.record(
                "normal dpkg install",
                "ok",
                f"{len(normal_debs)} debs, {installed_count} configured",
            )
        else:
            for e in err_msgs:
                log_error(e)
            summary.record(
                "normal dpkg install",
                "fail",
                f"{err_count} error(s): {'; '.join(err_msgs[:3])}",
            )
            log_warn("Continuing to force-depends step despite errors...")

        # ── Step 6: Force-depends install ─────────────────────────────────
        log_step(
            6,
            TOTAL_STEPS,
            f"Installing {len(force_debs)} packages with --force-depends (perl chain)",
        )
        if force_debs:
            log_info(
                "These packages depend on 'perl-openssl-abi-3', a virtual package "
                "provided by libssl3. --force-depends bypasses the static dpkg check "
                "safely because libssl3 is present on the system."
            )
            for pkg_path in force_debs:
                log_info(f"  → {os.path.basename(pkg_path)}")

            ok, installed_count, err_count, err_msgs = dpkg_install(
                force_debs, force_depends=True, dry_run=args.dry_run
            )
            if ok:
                log_ok(f"Force-depends install complete — {len(force_debs)} packages")
                summary.record(
                    "force-depends dpkg install",
                    "ok",
                    f"{len(force_debs)} perl packages installed",
                )
            else:
                for e in err_msgs:
                    log_error(e)
                summary.record(
                    "force-depends dpkg install",
                    "fail",
                    f"{err_count} error(s): {'; '.join(err_msgs[:3])}",
                )
        else:
            log_info("No packages require --force-depends")
            summary.record("force-depends dpkg install", "skip", "no packages needed it")

        # Settle any pending configurations
        ok, msg = dpkg_configure_all(dry_run=args.dry_run)
        if ok:
            log_ok(f"dpkg --configure -a: {msg}")
            summary.record("dpkg configure -a", "ok", msg)
        else:
            log_warn(f"dpkg --configure -a issue: {msg}")
            summary.record("dpkg configure -a", "warn", msg)

        # ── Step 7: Verify ─────────────────────────────────────────────────
        log_step(7, TOTAL_STEPS, "Verifying Ansible installation")

        if args.dry_run:
            log_info("[dry-run] skipping live verification")
            summary.record("ansible verification", "skip", "dry-run mode")
        else:
            ansible_ok = verify_ansible(summary)
            verify_perl_chain(summary)
            if ansible_ok:
                log_ok("Ansible is installed and responding correctly")
            else:
                log_error("Ansible verification failed — check errors above")

    finally:
        # Always clean up the temp directory
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
            log_info(f"Cleaned up working directory: {work_dir}")

    summary.print_report()

    # Exit code: 1 if any step failed
    if any(s["status"] == "fail" for s in summary.steps):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()