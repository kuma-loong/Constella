# Constella TUI

Constella TUI is a keyboard-first terminal client for an existing Constella manager. It reads the same realtime `/ws/cluster` stream as the Web UI, so sampling, aggregation, authentication boundaries, and cluster state remain in the backend.

## Run

After installing `constella-gpu`:

```bash
constella tui
# or
constella-tui
```

Connect to a remote manager through an SSH tunnel or a directly reachable endpoint:

```bash
constella tui --url http://127.0.0.1:8765
constella-tui --url https://gpu.example.com
```

`CONSTELLA_URL` can provide the default manager URL.

## Keyboard

| Key | Action |
| --- | --- |
| `Tab`, `Shift+Tab` | Move focus |
| Arrow keys, `j`, `k` | Move through nodes and table rows |
| `r` | Reconnect immediately |
| `?` | Open keyboard help |
| `q` | Quit |

The client reconnects automatically after a dropped connection. No agent token is needed because the cluster dashboard stream is intentionally read-only and follows the same access boundary as the Web UI.
