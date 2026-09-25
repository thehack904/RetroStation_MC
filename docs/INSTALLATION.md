# Installation

RetroStation MC can run either as a Docker container or as a local Python application.

## Requirements

### Docker path

- Docker Engine or compatible container runtime
- Docker Compose v2
- Network access from IPTV clients to the app host and port

### Local Python path

- Python 3.11 or newer
- FFmpeg available in `PATH`
- Python packages from `requirements.txt`

Runtime Python dependencies:

```text
Flask==3.0.3
Pillow==10.4.0
python-dateutil==2.9.0.post0
```

## Docker Compose installation

From the repository root:

```bash
docker compose up --build
```

The included compose file maps the container port to host port `8787` and persists the main writable directories:

```yaml
services:
  retro-guide-poc:
    build: .
    container_name: retro-guide-poc
    ports:
      - "8787:8787"
      - "65001:65001/udp"
    volumes:
      - ./data:/app/data
      - ./output:/app/output
      - ./sample_data:/app/sample_data
    environment:
      - RETROGUIDE_HOST=0.0.0.0
      - RETROGUIDE_PORT=8787
```

Open the admin UI:

```text
http://localhost:8787/
```

## Local Python installation

```bash
sudo ./retrostation_linux.sh install
```

The installer creates a dedicated `retrostation-mc` system user, installs the application under `/opt/retrostation-mc`, stages mutable state under `/var/lib/retrostation-mc`, and creates/starts the `retrostation-mc` systemd service. During installation it runs `gpu_hwaccel_detect_v3.py` to report whether hardware acceleration can be used or if software fallback is required.

### Fresh-install layout and permissions

| Path | Ownership / mode | Notes |
|---|---|---|
| `/opt/retrostation-mc` | root-managed application tree | code and `.venv`; the service does not write here |
| `/etc/retrostation-mc` | `root:root`, `0755` | administrator-managed configuration directory |
| `/etc/retrostation-mc/retrostation-mc.conf` | `root:root`, `0644` | optional environment overrides loaded by systemd |
| `/var/lib/retrostation-mc` | `retrostation-mc:retrostation-mc`, `0755` | database, generated HLS output, runtime files, migration state |
| `/var/lib/retrostation-mc/.migration` | root-managed, `0750` | restartable legacy-migration markers |
| `/var/backups/retrostation-mc` | root-managed, `0750` | legacy-layout backups and rollback instructions |

The dedicated service account is created with:

- service name and account: `retrostation-mc`
- primary group: `retrostation-mc`
- home directory: `/var/lib/retrostation-mc`
- no-login shell

If the host has `video` and/or `render` groups, the installer adds the service account to them so VA-API/QSV can access `/dev/dri` without reusing a shared `iptv` account.

The generated systemd unit stays at `/etc/systemd/system/retrostation-mc.service` and uses:

- `User=retrostation-mc`
- `Group=retrostation-mc`
- `WorkingDirectory=/opt/retrostation-mc`
- `EnvironmentFile=-/etc/retrostation-mc/retrostation-mc.conf`
- `ProtectSystem=full`
- `NoNewPrivileges=yes`
- `PrivateTmp=yes`
- `ProtectHome=yes`
- `ProtectKernelTunables=yes`
- `ProtectKernelModules=yes`
- `ProtectControlGroups=yes`
- `ReadWritePaths=/var/lib/retrostation-mc`

### Upgrade from `/home/iptv/retrostation-mc`

Run the normal installer:

```bash
sudo ./retrostation_linux.sh install
```

Migration is automatic when the legacy tree is detected. The installer:

1. Stops only the managed `retrostation-mc` service if it is already running.
2. Backs up the existing systemd unit, legacy service environment files, and legacy `data/`, `output/`, and `runtime/` trees under `/var/backups/retrostation-mc/...`.
3. Copies legacy config into `/etc/retrostation-mc/legacy/`.
4. Copies legacy state into `/var/lib/retrostation-mc` without overwriting already-migrated files.
5. Records restartable progress and completion markers under `/var/lib/retrostation-mc/.migration`.
6. Leaves `/home/iptv/retrostation-mc` in place so cleanup is always an explicit manual decision.

If installation is interrupted, re-run `sudo ./retrostation_linux.sh install`. The migration reuses the recorded backup path and continues from the remaining steps instead of starting over.

Verify the upgraded layout with:

```bash
sudo systemctl status retrostation-mc
sudo systemctl cat retrostation-mc
sudo journalctl -u retrostation-mc -n 100 --no-pager
```

Confirm the active unit points to `/opt/retrostation-mc/.venv/bin/python /opt/retrostation-mc/app.py` and that writable files are now under `/var/lib/retrostation-mc`.

### Backup and restore

Every automatic legacy migration writes a timestamped backup under `/var/backups/retrostation-mc`. Each backup contains:

- `systemd/` — the previously managed unit
- `config/` — legacy service env files and copied config files
- `state/` — copied `data/`, `output/`, and `runtime/`
- `rollback.txt` — a host-local rollback checklist

Recommended backup flow before major host maintenance:

```bash
sudo tar -C / -czf retrostation-mc-backup.tgz \
  etc/retrostation-mc \
  var/lib/retrostation-mc \
  var/backups/retrostation-mc
```

To restore onto a rebuilt host, restore `/etc/retrostation-mc` and `/var/lib/retrostation-mc`, reinstall with `sudo ./retrostation_linux.sh install`, then verify the service with `systemctl status` and `journalctl`.

### Rollback to the legacy layout

Automatic migration is not destructive, so rollback uses the retained legacy tree plus the generated backup:

1. Open the `rollback.txt` file inside the relevant `/var/backups/retrostation-mc/...` directory.
2. Stop the current service: `sudo systemctl stop retrostation-mc`
3. Restore the backed-up unit and any legacy service environment/config files from the backup.
4. Reinstall or restore any needed legacy runtime files under `/home/iptv/retrostation-mc`.
5. Reload systemd and restart the service.

Because `/home/iptv/retrostation-mc` is left in place, rollback can be performed even after an interrupted migration as long as the retained legacy tree and backup are still available.

### Safe uninstall versus purge

| Command | Removes | Retains |
|---|---|---|
| `sudo ./retrostation_linux.sh uninstall` | `/opt/retrostation-mc`, managed systemd unit | `/etc/retrostation-mc`, `/var/lib/retrostation-mc`, migration metadata, dedicated account |
| `sudo ./retrostation_linux.sh uninstall --purge` | above plus `/etc/retrostation-mc`, `/var/lib/retrostation-mc`, and the managed account/group when safe | legacy shared `/home/iptv` resources remain manual-review items |

The uninstaller never uses broad user-wide kill operations such as `pkill -u iptv`, never removes sibling services, and only removes the dedicated account when no running processes or project-owned files still depend on it.

### systemd status, logs, and troubleshooting

```bash
sudo systemctl status retrostation-mc
sudo systemctl restart retrostation-mc
sudo systemctl stop retrostation-mc
sudo systemctl start retrostation-mc
sudo systemctl is-active retrostation-mc
sudo systemctl is-enabled retrostation-mc
sudo journalctl -u retrostation-mc -n 100 --no-pager
sudo journalctl -u retrostation-mc -f
```

Common checks:

- `systemctl cat retrostation-mc` — confirm the installed unit uses `/opt/retrostation-mc` and `/etc/retrostation-mc/retrostation-mc.conf`
- `journalctl -u retrostation-mc` — inspect Python import failures, missing FFmpeg, or permissions problems
- `ls -ld /opt/retrostation-mc /etc/retrostation-mc /var/lib/retrostation-mc` — verify the expected split layout still exists

### Coexistence with sibling projects and RetroStation Player

RetroStation MC now uses dedicated Linux paths specifically so it can coexist with RetroStation Player, RetroIPTVGuide, and other sibling projects on the same host:

- it does not reuse `/home/iptv` for fresh installs
- it manages only the `retrostation-mc` systemd unit
- it writes only to `/opt/retrostation-mc`, `/etc/retrostation-mc`, `/var/lib/retrostation-mc`, and `/var/backups/retrostation-mc`
- legacy shared `/home/iptv` resources are left for manual review instead of being deleted automatically

Or run the setup steps manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app binds to `0.0.0.0:8787` by default.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RETROGUIDE_HOST` | `0.0.0.0` | Flask bind address |
| `RETROGUIDE_PORT` | `8787` | Flask port |
| `RETROGUIDE_HOST_ALIASES` | unset | Optional hostname-to-address overrides for playlist/XMLTV URLs (for example `media.lan=192.0.2.25`) |
| `RETRO_TELEMETRY_DEBUG` | disabled | Enables low-frequency structured renderer/HLS telemetry logs when set to `1`, `true`, `yes`, or `on` |

Example:

```bash
RETROGUIDE_HOST=127.0.0.1 RETROGUIDE_PORT=8787 python app.py
```

## Hostname-based tuner or EPG URLs fail in Docker

If `http://192.0.2.25:8409/iptv/channels.m3u` works but `http://media.lan:8409/iptv/channels.m3u` fails, the container likely cannot resolve your LAN/router DNS names (`.lan`, `.local`, and similar).

Use one of these fixes:

1. Use the source IP address directly.
2. Configure DNS for the container runtime.
3. Add a Docker `extra_hosts` mapping.
4. Set `RETROGUIDE_HOST_ALIASES` to map hostnames to reachable addresses.

Example:

```bash
RETROGUIDE_HOST_ALIASES=media.lan=192.0.2.25
```

Then this source can still be used in the app:

```text
http://media.lan:8409/iptv/channels.m3u
```

## Persistent storage

Persist these directories when containerized:

| Directory | Required | Contents |
|---|---:|---|
| `data/` | Yes | SQLite config, events, guide state, uploaded music, PID files |
| `output/` | Recommended | Current HLS playlists and segments |
| `sample_data/` | Optional | Example inputs; useful for first-run validation |

## First-run behavior

On startup, RetroStation MC:

1. Creates `data/config.db` if it does not exist.
2. Inserts default settings when the database is empty.
3. Builds `data/guide_state.json` from configured M3U/XMLTV data.
4. Generates `output/standby.ts`.
5. Shows the standby stream until the admin clicks **Save & Start**.

The guide pipeline does not auto-start on a clean first run.

## Validation commands

Check the admin UI:

```bash
curl -I http://localhost:8787/
```

Check the exported channel playlist:

```bash
curl http://localhost:8787/channel.m3u
```

Check the HLS master playlist:

```bash
curl http://localhost:8787/hls/master.m3u8
```

If the Weather Channel is enabled, check its dedicated HLS output too:

```bash
curl http://localhost:8787/hls/weather.m3u8
```

Check status JSON:

```bash
curl http://localhost:8787/status
```


## HDHomeRun Export network requirement

HDHomeRun Export uses two network services:

- TCP `8787` for RSMC HTTP discovery metadata, lineup data, and tune URLs.
- UDP `65001` for native HDHomeRun/libhdhomerun device discovery used by Plex and compatible DVR clients.

If a host firewall is enabled, allow inbound UDP 65001 from the local LAN. Do not expose the discovery port to the public Internet.
