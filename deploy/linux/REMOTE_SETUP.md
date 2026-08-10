# Remote Setup: Windows MT5 + Linux Bridge via Tailscale

## Linux VPS

```bash
sudo apt update
sudo apt install -y python3-venv
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

sudo mkdir -p /opt/forex_algo
sudo chown "$USER":"$USER" /opt/forex_algo
# copy repository to /opt/forex_algo
cd /opt/forex_algo
cp python/.env.example python/.env
```

Set these in `python/.env`:

```dotenv
LLM_API_KEY=...
LLM_BASE_URL=https://...
LLM_MODEL=ocGO/deepseek-v4-flash
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=8080
BRIDGE_REQUIRE_TOKEN=true
BRIDGE_TOKEN=<long-random-token>
```

Get the Linux Tailscale IP:

```bash
```

Install services:

```bash
docker compose up -d --build
curl http://127.0.0.1:8080/health
```

Do not open port 8080 publicly. Restrict access to the Tailscale interface/firewall.

## Windows MT5 VPS

```powershell
winget install Tailscale.Tailscale
```

Login to the same Tailscale tailnet and test:

```powershell
Test-NetConnection <LINUX_TAILSCALE_IP> -Port 8080
```

In MT5, whitelist:

```text
http://<LINUX_TAILSCALE_IP>:8080
```

Attach `RegimeEA` to XAUUSD M5 and set:

```text
InpServerUrl=http://<LINUX_TAILSCALE_IP>:8080
InpBridgeToken=<same token as Linux .env>
InpSignalOnly=true   # first observe; false only after plumbing passes
```

The token is sent inside the JSON payload because the current MT5 WebRequest overload does not reliably set custom request headers.

## Verify

```bash
docker compose ps
docker compose logs -f bridge
curl http://127.0.0.1:8080/health
```

Start with `InpSignalOnly=true`. Confirm decisions arrive for at least several bars, then switch to `false` on demo only.
