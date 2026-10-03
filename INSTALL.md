# Install Chiikawa

Chiikawa runs on **macOS, Linux, and Windows through WSL**, with **Python 3.10
or newer** on your PATH as `python3`. It uses Microsoft Foundry by default
(your own endpoint, deployment, and API key), or OpenRouter with your own API key.
Native Windows is not supported yet.

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

The interactive CLI displays its mascot and prompts with `chiikawa>`. Type `/`
to open the command menu immediately. Filter by typing; use ↑/↓, Tab, and Enter
to choose, or Esc to dismiss. `/model` and `/provider` open selection menus;
`/status` shows your current configuration and session. You can also type
`/provider openrouter` or `/model openai/gpt-5.4` directly. Changing either starts
a fresh conversation and preserves the previous session log. `/new` starts a
fresh conversation, `/help` lists commands, and `/exit` quits. In basic terminals,
type the command and press Enter (the live menu is unavailable).

Safe mode
asks before writes, shell commands, or delegation. Ctrl-D exits; Ctrl-C exits
with status 130. Use `chiikawa --resume` from the same project to continue its
latest session. Configuration comes from environment variables; `.env` files
are not loaded automatically. Installation, `--help`, and `--version` need no credentials.

To use OpenRouter instead, set its key and choose the provider:

```sh
export OPENROUTER_API_KEY="YOUR-OPENROUTER-KEY"
chiikawa --provider openrouter -d /path/to/your/project
# Optional model override (must support tools):
chiikawa --provider openrouter -m anthropic/claude-sonnet-4.6
```

OpenRouter defaults to `openai/gpt-5.4`; `OPENROUTER_MODEL` changes that default.
It uses only `OPENROUTER_API_KEY` and ignores Foundry configuration.
Foundry remains the default even when both keys are present. Optionally set
`CHIIKAWA_PROVIDER=openrouter` to select OpenRouter for your shell;
`--provider foundry` or `--provider openrouter` overrides that setting.
Resume with the original provider (`chiikawa --provider openrouter --resume`).
The saved OpenRouter model is restored automatically; changing it requires
a new session. Legacy sessions belong to Foundry.

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

The context meter shows estimated history size and the **600,000-token compaction
threshold**. Use `chiikawa --context-threshold 100000` to change that threshold.
Each completed response shows API input/output usage and the output limit
(**65,536 tokens for Foundry; 16,384 for OpenRouter** by default). Override it
with `--max-output-tokens 8192`; this also applies to compaction and child agents.
Choose a context threshold and output limit that fit your selected model.
Compaction requests are labeled separately. The context estimate
matches the compactor's history-only estimate, while API input counts cover the
actual request. Short histories can exceed the threshold until there are enough
messages to compact. Missing API usage is shown as `not reported`.

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
