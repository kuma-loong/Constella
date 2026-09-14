# Packaging Constella for PyPI

Constella 0.1.4 provides five composable distributions. Each feature is
owned by exactly one wheel, so installing variants together never overwrites a
shared Python package.

| Distribution | Backend/API | Web UI | Lab identity | TUI |
| --- | :---: | :---: | :---: | :---: |
| `constella-gpu` | Yes | Yes | No | Yes |
| `constella-gpu-web` | Yes | Yes | No | No |
| `constella-gpu-lab` | Yes | Lab UI | Yes | No |
| `constella-gpu-tui` | No | No | No | Yes |
| `constella-gpu-backend` | Yes | No | No | No |

Package ownership:

- `constella-gpu-backend` owns the `constella` module and `constella` command.
- `constella-gpu-web` owns only the `constella_web` static-asset package and
  depends on the backend.
- `constella-gpu-lab` owns `constella_lab`, the Lab static assets, and the
  `constella-lab` command. It depends on the backend, which never imports Lab.
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

The script copies Git-tracked working-tree files to a temporary directory under
`run/preview-package-*`. It builds both frontend editions and all five wheel/source
distribution pairs there, then places the ten archives in `dist/0.1.4/`. Existing
production assets, virtual environments, and older release archives are untouched.
New source files must be added to Git before building. The staging directory is
removed when the build exits; `dist/` remains ignored by Git.

An existing output directory must be empty. To use another location:

```bash
./scripts/package/build.sh --out-dir dist/review-0.1.4
```

Before upload, verify the artifact set and metadata:

```bash
ls -1 dist/0.1.4/
uvx 'twine>=7' check dist/0.1.4/*.whl dist/0.1.4/*.tar.gz
```

## Installed usage

Full installation:

```bash
pip install "constella-gpu==0.1.4"
constella service start
constella tui
```

Web-only frontend installation:

```bash
pip install "constella-gpu-web==0.1.4"
constella service start
```

Standalone TUI client installation:

```bash
uv tool install "constella-gpu-tui==0.1.4"
constella-tui --url https://gpu.example.com
```

Backend/API-only installation:

```bash
uv tool install "constella-gpu-backend==0.1.4"
constella service start --no-local-agent
```

Lab installation:

```bash
pip install "constella-gpu-lab==0.1.4"
constella-lab serve --host 127.0.0.1 --port 8765
```

Lab deliberately remains outside the full public monitoring distribution. See
[Lab deployment](LAB_DEPLOYMENT.md) for required fail-closed Access settings.

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
