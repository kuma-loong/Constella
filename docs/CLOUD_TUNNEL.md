# Cloudflare Tunnel

Cloudflare Tunnel is optional. Use it when you want to expose Constella through a domain while keeping the origin service bound to localhost and avoiding an inbound server port.

## Start Constella Locally

Keep the GPU service bound to localhost:

```bash
HOST=127.0.0.1 PORT=8765 ./scripts/service/start.sh
```

## Configure Cloudflare

In Cloudflare Zero Trust, add a Public Hostname to the tunnel:

```text
Hostname: https://gpu.example.com
Service:  http://127.0.0.1:8765
```

If the dashboard should not be public, protect the hostname with a Cloudflare Access policy.

## Install cloudflared

Install `cloudflared` as the current user:

```bash
mkdir -p ~/.local/bin
curl -fL \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o ~/.local/bin/cloudflared
chmod +x ~/.local/bin/cloudflared
~/.local/bin/cloudflared --version
```

## Store the Token

Store the Cloudflare tunnel token in a local private env file. Do not commit this file:

```bash
mkdir -p run
umask 077
cat > run/cloudflared.env <<'EOF'
CLOUDFLARED_TOKEN='paste-your-token-here'
EOF
chmod 600 run/cloudflared.env
```

`run/` is ignored by git.

## Start, Inspect, and Stop

```bash
./scripts/tunnel/start.sh
./scripts/tunnel/status.sh
./scripts/tunnel/stop.sh
```

The tunnel script passes the token through the `TUNNEL_TOKEN` environment variable, so it does not appear in command-line arguments. Logs are written to `logs/cloudflared.log`; the PID is written to `run/cloudflared.pid`.

## Security Notes

- The dashboard exposes usernames and process information. Protect the hostname with Cloudflare Access unless it is intentionally public.
- Keep `run/cloudflared.env` at mode `600`.
- Keep Constella bound to `127.0.0.1` unless you intentionally need another bind address.
- If a token leaks, rotate it in Cloudflare and update `run/cloudflared.env`.
