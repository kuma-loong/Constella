# Packaging Constella for PyPI

Constella 0.1.3 is published as four composable distributions. Each feature is
owned by exactly one wheel, so installing variants together never overwrites a
shared Python package.

| Distribution | Backend/API | Web UI | TUI |
| --- | :---: | :---: | :---: |
| `constella-gpu` | Yes | Yes | Yes |
| `constella-gpu-web` | Yes | Yes | No |
| `constella-gpu-tui` | No | No | Yes |
| `constella-gpu-backend` | Yes | No | No |

Package ownership:

- `constella-gpu-backend` owns the `constella` module and `constella` command.
- `constella-gpu-web` owns only the `constella_web` static-asset package and
  depends on the backend.
- `constella-gpu-tui` owns `constella_tui` and `constella-tui`. It is a remote
  client and deliberately does not depend on the backend.
- `constella-gpu` is the full meta distribution and depends on Web and TUI.

The backend discovers installed Web assets at runtime. If the Web distribution
is absent, API routes remain available and browser frontend routes are not
mounted. The `constella tui` subcommand is similarly registered only when the
TUI distribution is installed.

## Build all distributions

```bash
./scripts/package/build.sh
```

The script runs the frontend build into
`packages/web/src/constella_web/dist`, then uses the uv workspace to build four
wheels and four source distributions under `dist/`. Generated Web assets and
`dist/` are ignored by Git.

Before upload, verify the artifact set and metadata:

```bash
ls -1 dist/
uvx twine check dist/*
```

## Installed usage

Full installation:

```bash
pip install "constella-gpu==0.1.3"
constella service start
constella tui
```

Web-only frontend installation:

```bash
pip install "constella-gpu-web==0.1.3"
constella service start
```

Standalone TUI client installation:

```bash
uv tool install "constella-gpu-tui==0.1.3"
constella-tui --url https://gpu.example.com
```

Backend/API-only installation:

```bash
uv tool install "constella-gpu-backend==0.1.3"
constella service start --no-local-agent
```

`uv tool install` is intentionally used for the standalone TUI and backend
distributions because each owns an executable. The Web distribution is a
static-asset extension and the full distribution is a meta package, so they do
not duplicate dependency-owned entry points; install those two into a Python
environment with pip. This avoids two distributions claiming the same script
file during uninstall.

For the complete command reference, see [PyPI CLI Usage](PYPI_CLI.md).

## Source deployment

Source deployment remains available through `scripts/service/*`. The source
scripts build and serve `frontend/dist`. `CONSTELLA_FRONTEND_DIST` or
`constella serve --frontend-dir` can override frontend discovery.

## Safe smoke testing

Never reuse a production port, runtime directory, or database. Build, create a
temporary virtual environment for each distribution, install from `dist/` with
`uv pip`, and use temporary ports and paths for service tests. Backend and TUI
tests should confirm that no Web route is mounted; Web and full tests should
confirm that `/overview` serves the packaged frontend.
