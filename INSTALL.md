# Install Chiikawa

Chiikawa runs on **macOS, Linux, and Windows through WSL**, with **Python 3.10
or newer** on your PATH as `python3`. It uses Microsoft Foundry; you need your
own endpoint, deployment, and API key. Native Windows is not supported yet.

## Downloadable installer

Download `chiikawa-0.1.0.tar.gz` from the project's release assets. Extract it,
then install the command for your user:

```sh
tar -xzf chiikawa-0.1.0.tar.gz
cd chiikawa-0.1.0
python3 install.py
```

The installer checks the archive's SHA-256, verifies that it runs, and installs
one executable to `~/.local/bin/chiikawa`. It needs no administrator access,
package manager, or network connection. If prompted, add the printed PATH line
to your shell profile (`~/.zshrc` or `~/.bashrc`), then open a new terminal.
Use `python3 install.py --bin-dir /your/command/directory` for a custom location.
Checksums detect damaged downloads; obtain releases from the publisher you trust.

You can also run the downloaded `chiikawa.pyz` directly:

```sh
python3 chiikawa.pyz --help
```

## Isolated package installation

If you already use pipx or uv, download the wheel release asset and run **one**
of these commands. These tools put `chiikawa` on your PATH in an isolated environment:

```sh
pipx install ./chiikawa_harness-0.1.0-py3-none-any.whl
# Or:
uv tool install ./chiikawa_harness-0.1.0-py3-none-any.whl
```

Run `pipx ensurepath` or `uv tool update-shell` if your tool directory is not
on PATH. Both tools also accept a wheel's HTTPS download URL. To install from
a source checkout, run `pipx install .` or `uv tool install .` at its root.
The package name is `chiikawa-harness`; the command is `chiikawa`.
This project is not yet published on PyPI, so use a file, source path, or release URL.

## Configure and start

Set your resource endpoint, API key, and deployment name in your shell or secret
manager. The placeholder below must be replaced with your own key:

```sh
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com/openai/v1"
export AZURE_OPENAI_API_KEY="YOUR-API-KEY"
export CHIIKAWA_MODEL="YOUR-DEPLOYMENT-NAME"

chiikawa --version
chiikawa -d /path/to/your/project
```

The interactive CLI displays its mascot and prompts with `chiikawa>`. Safe mode
asks before writes, shell commands, or delegation. Ctrl-D exits; Ctrl-C exits
with status 130. Use `chiikawa --resume` from the same project to continue its
latest session. Configuration comes from environment variables; `.env` files
are not loaded automatically. Installation, `--help`, and `--version` need no credentials.

For this checkout's existing `.env`, load it before starting:

```sh
set -a
source .env
set +a
chiikawa
```

The terminal shows public reasoning summaries, progress updates, tools, file
reads, edit diffs, and command exit codes. The CLI requests medium reasoning
effort; `--no-reasoning` uses the deployment's default and disables summaries.
Summaries appear after each model response, when available. Activity is written
to stderr, leaving completed answers on stdout for redirection. Colors honor
`NO_COLOR`, and redirected activity has no animation or terminal escape codes.

For scripts, use `chiikawa -p "your task"`. Headless mode defaults to `yolo`;
add `--mode safe` or `--mode read-only` when appropriate. Shell commands run
with your user permissions. See `chiikawa --help` for all options.

## Upgrade and remove

For the downloadable installer, download and extract the new release and rerun
`python3 install.py`. It atomically replaces an existing Chiikawa zipapp and
refuses to overwrite an unrelated command or package-manager symlink.
Remove this installation with `rm ~/.local/bin/chiikawa` (adjust for a custom
`--bin-dir`). Keep Python installed while using the CLI.

For pipx, install the new wheel with `pipx install --force ./NEW-WHEEL.whl`;
remove with `pipx uninstall chiikawa-harness`. For uv, use
`uv tool install --force ./NEW-WHEEL.whl` and `uv tool uninstall chiikawa-harness`.
Choose one installation method to avoid command-name conflicts.

Upgrading or removing the command preserves each project's `.chiikawa/` session
history and `CHIIKAWA.md` memory.

## Build and distribute a release

From a source checkout:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
uv build
python3 scripts/check_dist.py
```

`dist/` contains the portable installer archive, executable zipapp, wheel, source
distribution, and SHA-256 manifest. The portable builder uses only Python's
standard library; `uv build` downloads the setuptools build dependency.
Release contents exclude credentials, workspaces, generated products, and session logs.

The GitHub Actions release workflow builds and verifies these files on a
`v*` tag, then creates a **draft** GitHub release with the downloads attached.
Set the version in `chiikawa/_version.py`, push a matching tag (for example,
`v0.1.0`), review the draft, and publish it to make the downloads available.
No GitHub repository is hardcoded into the installer. A release host must be
configured and the first release published before public downloads are available.
