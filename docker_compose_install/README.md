# docker_compose_install

Offline installer for the Docker Compose v2 CLI plugin. Bundles the binary so
no internet access is needed on the target server.

**Included binary:** Docker Compose v2.39.4, Linux x86-64 (amd64)

## Requirements

- Linux x86-64 (amd64) server — the binary will not work on ARM64 or other architectures
- Docker Engine already installed (the `docker` CLI must be present)
- `sudo` access (needed for the system-wide plugin install)

## Usage

1. Copy the entire folder to the home directory of the target server:

   ```bash
   scp -r docker_compose_install/ user@your-server:~/
   ```

2. SSH into the server and run the installer:

   ```bash
   ssh user@your-server
   chmod +x ~/docker_compose_install/install_compose_plugin.sh
   ~/docker_compose_install/install_compose_plugin.sh
   ```

3. Verify it works:

   ```bash
   docker compose version
   ```

## What the script does

- Validates the bundled binary is present and executable
- Copies it to `/usr/local/lib/docker/cli-plugins/docker-compose` (system-wide, requires `sudo`)
- Copies it to `~/.docker/cli-plugins/docker-compose` (current user only, no `sudo`)
- Runs `docker compose version` to confirm the plugin is picked up by the Docker CLI

After installation, `docker compose` (with a space, not a hyphen) will work for all users.

## Notes

- The binary is statically linked — no extra runtime dependencies required.
- The folder name on the server must be `docker_compose_install` directly under `$HOME`
  (i.e. `~/docker_compose_install/`). The script hardcodes this path.
- If your server uses a non-root user without `sudo`, the system-wide install will fail
  but the user-level install under `~/.docker/cli-plugins/` will still succeed.
