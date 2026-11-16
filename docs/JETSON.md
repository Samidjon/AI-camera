# Edge agent on Linux and NVIDIA Jetson

Target devices: x86_64 mini PCs (Ubuntu 22.04 / 24.04, Debian 12) and NVIDIA Jetson (Orin Nano / NX /
AGX with JetPack 6, Ubuntu 22.04 aarch64). The agent runs **on this computer**, next to the cameras —
not inside the cameras.

> Status: scripts are written and syntax-checked; they have **not yet been run on real Linux / Jetson
> hardware** in this project. Please report the first install's output.

## 1. Prepare

```bash
sudo apt update && sudo apt install -y python3 ca-certificates
# uv (Python package manager) — official installer, review it first:
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh && less /tmp/uv-install.sh
sh /tmp/uv-install.sh && sudo install -m 755 ~/.local/bin/uv /usr/local/bin/uv
```

JetPack ships Python 3.10; the agent needs 3.12 — `uv` installs a private 3.12 under
`/opt/aci-agent/python` automatically. Nothing in the system Python changes.

## 2. Install

```bash
git clone <your repository> && cd ai-camera-intelligence/edge/camera-agent
# optional: put the YOLOX model into models/ first (see docs/AI_ENGINE.md)
sudo ./deploy/linux/install.sh            # add --with-claude for the optional Claude attributes
sudo aci-agentctl configure               # JWT secret, device name, dashboard URL, central server
sudo aci-agentctl restart
aci-agentctl status
```

What the installer does: creates the system user `aci-agent` (no shell, member of `video`), copies the
code to `/opt/aci-agent` (root-owned, read-only for the service), builds the virtualenv with
`uv sync --frozen` (exact locked versions), writes `/etc/aci-agent/agent.env` (mode 600), creates
`/var/lib/aci-agent` (mode 700), installs and enables the hardened systemd unit and `aci-agentctl`.

To reach the agent from the dashboard on another computer, set `ACI_AGENT_HOST=0.0.0.0` and the
dashboard origin in `ACI_AGENT_CORS_ORIGINS`, and allow port 8100 only from your LAN
(`sudo ufw allow from 192.168.1.0/24 to any port 8100`).

## 3. Operate

```bash
sudo aci-agentctl start | stop | restart
aci-agentctl status | health
aci-agentctl logs -f                      # journald
sudo aci-agentctl backup | backups | restore <id>
sudo aci-agentctl register --server https://server.example.com
```

The service restarts automatically after a crash (`Restart=always`) and starts at boot.

## 4. Update (verified, with rollback)

On a build machine: `./deploy/package.sh` → `dist/aci-agent-<version>.tar.gz` + `.sha256`.
On the device:

```bash
sudo aci-agentctl update aci-agent-0.5.0.tar.gz --sha256-file aci-agent-0.5.0.tar.gz.sha256
```

The checksum must match; then data is backed up, the current code kept as `/opt/aci-agent.prev`, the
new version installed and health-checked. If it doesn't answer within 30 s the previous version is
restored automatically (`sudo aci-agentctl rollback` does it manually).

## 5. Uninstall

```bash
sudo aci-agentctl uninstall            # keeps /var/lib/aci-agent and /etc/aci-agent
sudo aci-agentctl uninstall --purge    # also deletes data, configuration and the user
```

## Jetson notes

* **Board detection:** the agent reads `/proc/device-tree/model` and reports `is_jetson`, GPU load
  (`/sys/devices/**/gpu.0/load`) and all thermal zones on the Devices page.
* **GPU inference:** the pip OpenCV wheel has no CUDA, so YOLOX runs on the CPU (the agent reports
  `opencv_cuda: false`). CUDA-enabled OpenCV for Jetson must be built from source against JetPack's
  CUDA/cuDNN and installed into `/opt/aci-agent/.venv`; then select backend **cuda** on the AI Engine page
  (it falls back to CPU, and says so, when CUDA isn't available). Not part of the installer.
* **Power mode / clocks:** `sudo nvpmodel -q`, `sudo jetson_clocks` — set to the mode you need; the
  agent's memory guard (`ACI_AGENT_VISION_MAX_RSS_MB`) and AI `max_inference_fps` keep it inside limits.
* **Storage:** prefer NVMe or a good SD card; `ACI_AGENT_EVENTS_MAX_DB_MB` and the free-disk floor
  protect the card from filling up.
* **USB cameras:** `/dev/video*` — the service user is in the `video` group.
