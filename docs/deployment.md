# Deployment

This page covers what a Rez administrator does: build packages with `griptape-nodes-rez`, keep
them in stores, build from each platform, and release with Rez's own tools. For how the pieces fit
together, see the [overview](overview.md).

## The build tool

Packages are built by `griptape-nodes-rez`, the command in the `griptape-nodes-rez-build` package.
It is separate from the engine: artists and the engine never run it. Install it with uv, or run it
from a checkout:

```bash
uv tool install git+https://github.com/griptape-ai/griptape-nodes-rez-build
# or
alias griptape-nodes-rez='uv run --python 3.12 --project /path/to/griptape-nodes-rez-build griptape-nodes-rez'

griptape-nodes-rez --help
```

It needs:

- **Rez 3's tools** (`rez`, `rez-env`, `rez-config`, `rez-search`). Pass their folder with
  `--rez-tools PATH` (every command takes it), set `GRIPTAPE_NODES_REZ_TOOLS`, or put `rez-env` on
  `PATH`. In a standard Rez install the folder is `<prefix>/bin/rez`.
- **The `platform`, `arch`, `os`, and `python` packages** that `rez bind` creates, for the Python
  version the tool runs with. Packages are built for that Python, so run the tool with the Python
  your `python` Rez package provides (`uv run --python 3.12 ...`).
- **git**, for `--git-url` and for declared library dependencies.

uv comes with the tool. uv settings in your environment (`UV_INDEX_URL`, `UV_FIND_LINKS`,
`UV_OVERRIDE`, ...) apply to every resolve, except `UV_TORCH_BACKEND`, which is refused (see
[GPU builds per machine](gpu-builds.md)).

It has four commands:

```
griptape-nodes-rez build-engine  --local-packages-path PATH [--engine-version V | --engine-repo PATH]
                                 [--no-editor] [--app-version V] [--rebuild] [--yes]
griptape-nodes-rez build-library (--git-url URL [--branch REF] | --local-path PATH) --local-packages-path PATH
                                 [--torch-backend B ...] [--rebuild] [--yes]
griptape-nodes-rez write-launch  --tools PLATFORM=PATH ... --library FAMILY[-VERSION] ... --local-packages-path PATH
                                 [--testing-store PLATFORM=PATH] [--config-file PLATFORM=PATH] [--set NAME=VALUE ...]
                                 [--allow-sandbox] [--engine-version V] [--version V] [--print] [--rebuild] [--yes]
griptape-nodes-rez gpu-config    [--local-packages-path PATH ...]
```

`griptape-nodes-rez --verbose <command> ...` shows each package as it is resolved and written.

- Every build writes only to the store you pass with `--local-packages-path`. Nothing is written to
  your released packages, and the tool never changes your Rez configuration.
- Every build is resolved with Rez before it counts as done, and a build that fails that check
  exits with an error. The check points Rez at the store being built
  (`rez env --paths <store>:<packages_path>`), so the store does not need to be on your search path
  while you build.
- Nothing is left half-written: package contents are staged next to their destination and renamed
  into place, and `package.py` is written last, itself through a temporary file and a rename.
- `--yes` never prompts. A missing setting or requirement stops the command with the reason.

## Stores

Two kinds of store are enough:

| Store | Written by | Read by |
|:--|:--|:--|
| **Local store** (`--local-packages-path`) | `griptape-nodes-rez` | Administrators testing a build |
| **Released packages** (wherever your studio releases to) | You, with `rez-cp`, once a build is tested | Artists, through your studio's `packages_path` |

To test from the local store (starting `gtn`, and the `rez env` each worker runs), Rez must search
it. Put it in a rezconfig of its own and layer that after your studio's:

```python
# griptape_testing_rezconfig.py
packages_path = ModifyList(prepend=["/studio/griptape/rez/packages/local"])
```

`prepend` puts the local store in front, so a build under test wins over a released one. Always use
`ModifyList`, so your studio's list is extended, not replaced
([Configuring Rez](https://rez.readthedocs.io/en/stable/configuring_rez.html)). Add the file to
`REZ_CONFIG_FILE` in the shell that starts `gtn`, and give it to the launch package with
`--config-file` so every worker's `rez env` reads it too (see [the launch package](#5-the-launch-package)).

If a store is on a network or cloud drive (SMB, LucidLink), Rez's default hard-link file locks can
fail, especially on Windows. Use directory locks in the rezconfig that adds the store:

```python
plugins = {
    "package_repository": {
        "filesystem": {
            "file_lock_type": "mkdir",
        }
    }
}
```

## 1. System packages

Rez resolves `platform`, `arch`, `os`, and `python` for every environment. Bind them once per
platform with `rez bind` if your studio has not. A build stops when they are missing.

## 2. The engine

```bash
griptape-nodes-rez build-engine \
    --local-packages-path /studio/griptape/rez/packages/local
```

This builds the published Griptape Nodes engine: the newest release the `gtn` app accepts. To
build a specific release, add `--engine-version 0.102.1`.

If your studio changes the engine itself, build your checkout instead:

```bash
griptape-nodes-rez build-engine \
    --engine-repo /path/to/griptape-nodes-engine \
    --local-packages-path /studio/griptape/rez/packages/local
```

The checkout is read as files, never imported, and its version comes from its `pyproject.toml`.
Give a changed engine a version no release uses, so it cannot be mistaken for one.

Either way, this writes:

- `griptape_nodes_engine`: the engine. Its `requires` are the version **ranges** the engine
  declares (and its supported Python versions), not the versions this build installed, so the
  engine resolves together with libraries wherever their ranges overlap.
- `griptape_nodes`: the Griptape Nodes app, which provides `gtn`.
- `griptape_nodes_editor_dist`: the prebuilt editor that `gtn` serves locally. `--no-editor` leaves
  it out. The served editor connects over the local `websocket_direct` transport, which is off by
  default: enable it in the project's `griptape_nodes_config.json`
  (`"transports": {"websocket_direct": {"enabled": true}}`).
- A package for every pip dependency.

The engine package requires the app and the editor at exactly the versions this build installed
(for example `griptape_nodes-0.99.0`). The app accepts a range of engine versions, and the build
resolves it against the one engine it packages, so Rez must not swap in a different app later.
`--app-version` picks the app version; otherwise the newest is used. The app and the engine must
come from matching releases: a worker from one release may not register with an engine from
another.

An existing engine package of the same version is kept, and the build only adds what this machine
needs (its variants of compiled dependencies). `--rebuild` replaces the engine package and
reinstalls this machine's variants of every dependency.

The check: `rez env griptape_nodes_engine==<version>` resolves from the store, and the engine, the
app, the editor, and `gtn` all come from it.

Console scripts are written as launchers that run the context's `python`, so they work on every
machine, not only the one that built them. Packages with tools put their `bin` folder at the front
of `PATH`.

## 3. Libraries

```bash
griptape-nodes-rez build-library \
    --git-url https://github.com/griptape-ai/griptape-nodes-library-standard.git \
    --local-packages-path /studio/griptape/rez/packages/local

griptape-nodes-rez build-library \
    --local-path /studio/libraries/my-library \
    --local-packages-path /studio/griptape/rez/packages/local
```

Each library becomes two packages: `<family>_edit`, which the editor loads, and `<family>`, which a
worker runs in. `<family>` is the repo or folder name in lowercase with underscores
(`griptape-nodes-library-standard` becomes `griptape_nodes_library_standard`), never the library's
display name. See [Libraries](libraries.md) for what each package contains, how its version is
chosen, and how library dependencies work.

- Libraries the manifest declares as dependencies are cloned and built first when the store has no
  `_edit` package for them. `--rebuild` rebuilds them (and their dependencies) as well.
- Existing packages of the same version are kept unless you pass `--rebuild`.
- The check: both packages resolve from the store, and `GTN_LIBRARY_PATHS` and
  `GTN_LIBRARY_WORKER_REQUESTS` hold this library's manifest and worker request.

For libraries that use torch, `--torch-backend` builds several GPU builds at once. See
[GPU builds per machine](gpu-builds.md).

## 4. One store, several platforms

Keep the local store where every platform can reach it, and run the same builds from each platform,
one at a time. Each build adds its platform's variants and keeps what other platforms built:

- Pure-Python packages have one variant for every platform, with platform-specific dependencies
  chosen when Rez resolves them.
- Compiled packages get one variant per platform, arch, Python, and torch build. A build adds this
  machine's variant without touching the others.
- Engine and library packages carry no variants and are kept when they already exist, so a second
  platform's build only adds its variants.

Two builds writing the same package at the same moment can lose a variant, so build one platform at
a time.

## 5. The launch package

```bash
griptape-nodes-rez write-launch \
    --tools linux=/mnt/pipeline/rez/bin/rez \
    --tools osx=/Volumes/pipeline/rez/bin/rez \
    --tools windows=P:/rez/bin/rez \
    --library griptape_nodes_library_standard \
    --library griptape_nodes_library_diffusers-0.40.1 \
    --set XDG_CONFIG_HOME=/studio/projects/my_show/config \
    --local-packages-path /studio/griptape/rez/packages/local
```

This writes `griptape_launch/<version>/package.py`. It requires `griptape_nodes_engine==<version>`
(the newest, or `--engine-version`) and `<family>_edit==<version>` for each `--library` (the newest
in the store or on Rez's search path when no version is given). Its `commands()` configure the
engine for the platform the environment resolves on, and stop with a message on a platform you did
not give with `--tools`. See [Configuration](configuration.md#the-launch-package) for exactly what it
sets.

- `--tools` is the folder holding Rez's tools on each platform, as an absolute path on that
  platform. `PLATFORM` is `linux`, `osx`, or `windows`.
- Without any `--library`, the package provides the engine only, and the command warns that no
  libraries will load.
- `--allow-sandbox` (off by default) lets artists keep a Sandbox Library in this environment. It
  loads in the editor's process with no virtual environment, so it can import only the engine and
  the libraries' edit-time packages in this launch environment; everything else stays strict. See
  [Allowing a sandbox library](configuration.md#allowing-a-sandbox-library).
- `--config-file PLATFORM=PATH` appends a rezconfig to `REZ_CONFIG_FILE`, after your studio's, so
  every worker's `rez env` reads it. For a testing launch package, this is how workers find the local
  store: give it the `ModifyList(prepend=[...])` file from [Stores](#stores).
- `--testing-store PLATFORM=PATH` only records the store in the package (as `testing_stores`); it
  sets nothing. The engine reads no Rez settings, so Rez itself has to find the store through
  `--config-file`. The tool warns when a testing store is given without one.
- `--version` defaults to one patch above the newest launch package in the store. An existing
  version is replaced only with `--rebuild`. `--print` shows the package without writing it.
- The check (on a platform the package is set up for): `rez env griptape_launch==<version>` resolves,
  the engine and app come from the stores, the settings are set, and each library's worker request
  resolves the way the worker prefix will run it.

The shell that runs `rez env griptape_launch` must already see the store the launch package is in:
the package cannot add the store it is found in.

Artists then run:

```bash
rez env --norc griptape_launch -- gtn
```

See [Configuration](configuration.md#starting-gtn-with---norc) for why `--norc` matters on macOS and
Linux. For when to use a launch package and when to use a launcher profile or project config
instead, see [Choosing environments](choosing-environments.md).

## 6. Releasing

Promote tested packages to your released packages with Rez's own tools, library by library, then
the engine, then the launch package last. Griptape Nodes does not do this for you. See
[Releasing](releasing.md) for the steps and an example script.

## Checklist

- [ ] `griptape-nodes-rez` installed, with Rez's tools (`--rez-tools`) and a Python matching your `python` package
- [ ] `platform`, `arch`, `os`, and a matching `python` bound on each platform
- [ ] A local store on a location every platform can reach, with directory file locks on network or cloud drives
- [ ] Engine (with `gtn` and the editor) built from each platform you support, one at a time: the published release, or your checkout with `--engine-repo`
- [ ] Libraries built from each platform, with the torch builds your workstations need ([GPU builds per machine](gpu-builds.md))
- [ ] Each GPU machine's rezconfig has the `implicit_packages` line `gpu-config` prints ([GPU builds per machine](gpu-builds.md))
- [ ] Launch package written with the libraries this environment needs ([Choosing environments](choosing-environments.md)); for testing, with `--config-file` pointing at a rezconfig that searches the local store
- [ ] `websocket_direct` enabled in the project's `griptape_nodes_config.json`
- [ ] Packages validated and released with `rez-cp`, launch package last
- [ ] Artists start with `rez env --norc griptape_launch -- gtn`, or a launcher profile that does the same
