# griptape-nodes-rez-build

Builds [Griptape Nodes](https://docs.griptapenodes.com) as [rez](https://rez.readthedocs.io)
packages, so a studio can run it the way it runs its other tools:

```bash
rez env griptape_launch -- gtn
```

The guides in [`docs/`](docs/overview.md) walk through deploying, configuring, choosing libraries,
GPU builds per machine, and releasing. This page is the command reference.

## Quick start

```bash
uv tool install git+https://github.com/griptape-ai/griptape-nodes-rez-build
griptape-nodes-rez --help
```

From a checkout, run `uv run --project <checkout> griptape-nodes-rez ...` instead.

## What do I need?

- **rez 3** (tested with 3.4.0), for its tools: `rez`, `rez-env`, `rez-config`, `rez-search`.
  Pass the folder with `--rez-tools`, set `GRIPTAPE_NODES_REZ_TOOLS`, or put `rez-env` on `PATH`.
  In a standard rez install the folder is `<prefix>/bin/rez`.
- **The `platform`, `arch`, `os`, and `python` packages** that `rez-bind` creates, for the Python
  version the tool runs with. Every build is resolved with rez before it counts as done, so builds
  stop without them.
- **Python 3.12 or 3.13.** Packages are built for the Python running the tool, so run it with the
  Python your `python` rez package provides (`uv run --python 3.12 ...`).
- **git**, for `build-library --git-url` and declared library dependencies.

Wheels match that Python, ignoring any virtualenv or other Python on `PATH`. A
wheel built for a different Python stops the build, naming the package.

uv comes with the tool. `UV_INDEX_URL`, `UV_FIND_LINKS`, `UV_OVERRIDE`, and other uv settings in
the environment apply to every resolve unchanged. `UV_TORCH_BACKEND` is refused (see
[Torch builds](#torch-builds)).

The tool never changes rez configuration. It runs rez with the environment it was started in.
Its checks use `rez env --paths <store>:<packages_path>`, so the store does not need to be on your
search path while you build.

## What does each command do?

`griptape-nodes-rez` has four commands. Three write packages into a package store, and `gpu-config`
prints a line for this machine's rezconfig:

| Command | Writes |
| :-- | :-- |
| `build-engine` | `griptape_nodes_engine` (the engine), `griptape_nodes` (the `gtn` app), `griptape_nodes_editor_dist` (the editor), every pip dependency |
| `build-library` | `<family>_edit` and `<family>` for a node library, its declared libraries, and every pip dependency |
| `write-launch` | `griptape_launch`, which artists start Griptape Nodes with |
| `gpu-config` | nothing: prints the rezconfig line that picks this machine's torch build |

Every command takes `--rez-tools PATH`, the folder holding rez's tools (see [What do I need?](#what-do-i-need)). The top-level `--verbose` shows each package as it is
resolved and written (`griptape-nodes-rez --verbose build-engine ...`).

The engine contains no rez code. It reads what these packages set: `GTN_LIBRARY_PATHS`,
`GTN_LIBRARY_WORKER_REQUESTS`, `GTN_CONFIG_LIBRARY__PROVISIONED_BY`, and
`GTN_CONFIG_WORKER__COMMAND_PREFIX`, plus `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED` when a sandbox is
allowed.

### build-engine

```bash
griptape-nodes-rez build-engine --local-packages-path PATH [--engine-version V | --engine-repo PATH] \
    [--no-editor] [--app-version V] [--rebuild] [--yes] [--rez-tools PATH]
```

By default the engine is the newest published `griptape-nodes-engine` release the app accepts.
`--engine-version V` picks another release.

To package your own engine changes, pass `--engine-repo` with a checkout instead (read as files,
never imported). Its version comes from its `pyproject.toml`. It cannot be combined with
`--engine-version`.

- `--no-editor` leaves out `griptape_nodes_editor_dist`.
- `--app-version V` picks the app version. Otherwise uv picks the newest.
- An existing engine package of the same version is kept, and the build only adds this machine's
  variants of compiled dependencies. `--rebuild` replaces the engine package and reinstalls this
  machine's variants of every dependency.

#### Which versions does the engine package require?

The version ranges the engine declares (from the release's metadata or the checkout's
`pyproject.toml`, with `requires-python`), not the versions this build resolved. So the engine
resolves together with node libraries wherever their ranges overlap.

The engine's optional extras are left out. Reproducibility comes from released package sets and
`rez env --time`.

The app and editor are pinned to the versions this build installed (`griptape_nodes-0.99.0`).
The app accepts a range of engine versions, and the build resolves it against the one engine it
packages. So rez must not swap in a different app later.

**The check:** `rez env griptape_nodes_engine==<version>` resolves from the store. `griptape_nodes`,
`griptape_nodes_app`, the editor, and `gtn` all come from it.

### build-library

```bash
griptape-nodes-rez build-library (--git-url URL [--branch REF] | --local-path PATH) \
    --local-packages-path PATH [--torch-backend B ...] [--rebuild] [--yes] [--rez-tools PATH]
```

Packages a node library as two packages, named after the repository or folder it was built from
(`griptape-nodes-library-standard` -> `griptape_nodes_library_standard`), never its display name.

**`<family>_edit`** holds the library's source and manifest under
`python/<repository or folder name>/`. The engine recognises a declared library dependency by that
folder name in the manifest's path.

It requires the library's `pip_dependencies` and the `_edit` packages of the libraries it declares
(`library_dependency`). A dependency with `required: false` becomes a weak `~<family>_edit`. Its
`commands()` publish the library to the engine:

```python
env.PYTHONPATH.append("{root}/python/griptape-nodes-library-rez-test")
env.GTN_LIBRARY_PATHS.append("{root}/python/griptape-nodes-library-rez-test/griptape_nodes_library.json")
env.GTN_LIBRARY_WORKER_REQUESTS.append(literal("Rez Test Library=griptape_nodes_library_rez_test==0.1.0"))
```

**`<family>`** requires `<family>_edit==<version>` and the `pip_dependencies_exec`. A worker for
the library runs in `rez env griptape_nodes_engine==<engine> <family>==<version>`. A library with
no execution dependencies still gets this package, so every worker request looks the same.

Both packages use the manifest's version ranges.

Declared library dependencies are cloned and built first when the store has no `_edit` package
for them that resolves on this machine. `--rebuild` always rebuilds them and their dependencies.
Existing packages of the same version are kept unless `--rebuild`.

#### Where does the library version come from?

First match wins:

1. A `pyproject.toml` in the manifest's folder, or up to three folders above it. It never looks
   above the git repository root.
2. `git describe --tags`.
3. The manifest's `library_version`.
4. `1.0.0`.

**The check:** both packages resolve from the store. `GTN_LIBRARY_PATHS` and
`GTN_LIBRARY_WORKER_REQUESTS` hold this library's manifest and worker request.

### write-launch

```bash
griptape-nodes-rez write-launch --tools PLATFORM=PATH ... --library FAMILY[-VERSION] ... \
    --local-packages-path PATH [--testing-store PLATFORM=PATH] [--config-file PLATFORM=PATH] \
    [--set NAME=VALUE ...] [--allow-sandbox] [--engine-version V] [--version V] [--print] [--rebuild] [--yes] \
    [--rez-tools PATH]
```

It requires `griptape_nodes_engine==<engine>` and each chosen library's
`<family>_edit==<version>`, the newest in the store or on rez's search path when no version is
given. Without `--library` the package provides the engine only, and the command warns that no
libraries will load.

- `--tools` paths must be absolute on their platform. `PLATFORM` is `linux`, `osx`, or `windows`.
- `--print` shows the package on stdout instead of writing it. The summary and notices go to stderr.
- `--version` defaults to one patch above the newest launch package in the store. An existing
  version is replaced only with `--rebuild`.

#### What does the launch package set?

Its `commands()` pick the settings for `system.platform`, and stop on a platform not given with
`--tools`.

- `GTN_CONFIG_LIBRARY__PROVISIONED_BY=environment`: the environment, not the engine, provides the
  libraries.
- `GTN_CONFIG_WORKER__COMMAND_PREFIX=["<tools>/rez", "env", "--norc", "griptape_nodes_engine=={engine_version}", "python-{python_version}", "{library_request}", "--"]`.
  It is written with `literal()` so rez leaves the placeholders for the engine to fill.
- `--set` values on every platform (`XDG_CONFIG_HOME`, for example).
- With `--allow-sandbox` (off by default), `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED=true` on every
  platform.

#### What does --allow-sandbox allow?

The engine also loads the sandbox library (its `sandbox_library_directory`) in the editor's own
process, and artists can add sandbox nodes and refresh the sandbox.

No virtualenv is built for it. Its imports must come from this launch environment, so a sandbox node
needing a package missing from it fails to load.

Everything else stays strict: only the libraries the environment publishes load, and nothing is
downloaded or installed. Without the option the setting stays unset, and the engine keeps the
sandbox off in environment mode.

#### How does --config-file reach the workers?

The file is appended to `REZ_CONFIG_FILE` after the studio's configuration, so every worker's
`rez env` reads it too. No rez configuration change is needed.

Rez replaces an inherited variable the first time a package appends to it, unless the studio lists
it in `parent_variables`. So the package reads the studio's value with rez's `getenv()` and sets
it back before appending. `append()` uses each platform's own separator.

A testing launch package uses this to find a local store. Give a rezconfig with
`packages_path = ModifyList(prepend=["<store>"])`.

`--testing-store` (testing only) is recorded in the package as `testing_stores` and sets nothing.
The engine reads no rez settings, so rez itself has to find the store.

#### How is the launch package checked?

The package needs `--tools` for the platform `write-launch` runs on, so it can be checked there.
Otherwise nothing is written. Use `--print` for a package aimed only at other platforms.

The check: `rez env griptape_launch==<version>` resolves from rez's configuration, the engine and
app come from the stores, and the settings above are set. From inside that environment, each
library's worker is started through the prefix the way the engine starts it. A store rez does not
search yet is added for the check with `REZ_PACKAGES_PATH`.

When the launch package does not resolve, each library is resolved with the engine, then in pairs.
The message names the libraries that conflict and on what package. A launch package that fails its
check is removed from the store.

### gpu-config

```bash
griptape-nodes-rez gpu-config [--local-packages-path PATH ...] [--rez-tools PATH]
```

Prints this machine's rezconfig line, from `nvidia-smi` and the torch builds in the store (or on
rez's search path):

```python
# Torch build for render-042 (linux-x86_64): NVIDIA driver supports CUDA 12.8; builds in the store: cpu, cu118, cu128.
# Add to this machine's rezconfig (never to one shared by machines with different GPUs):
implicit_packages = ModifyList(append=[".torch_backend-cu128"])
```

It picks the newest CUDA build the driver supports, or `cpu` without an NVIDIA GPU. On macOS
there is a single torch build and nothing to configure.

## Torch builds

On Linux and Windows, `build-library --torch-backend cu128 --torch-backend cpu` (or `all`)
installs torch once per build. Each becomes a variant carrying the ephemeral
`.torch_backend-<build>`, so one store serves every GPU generation.

Each machine chooses its build with `implicit_packages`, the approach the rez documentation
describes for GPU packages (*Ephemeral Packages*). `gpu-config` prints the line.

With `--torch-backend`, the manifest's torch source is replaced by each chosen build. On macOS
`--torch-backend` is ignored.

### Why is --torch-backend=auto refused?

`--torch-backend=auto` in a manifest's `pip_install_flags`, and `UV_TORCH_BACKEND`, make uv pick
torch for the GPU of the build machine. That build would land in a store every workstation shares.

### The guard

Rez satisfies an ephemeral in a variant just by resolving it. Without a build in the machine's
rezconfig, rez would quietly pick a CUDA variant. Every package with a variant per
torch build (torch, torchvision, ...) therefore checks in its `commands()`:

```python
builds = [str(r) for r in this.variant_requires if str(r).startswith('.torch_backend-')]
if builds and '.torch_backend' not in implicits and '.torch_backend' not in request:
    stop(... 'run "griptape-nodes-rez gpu-config" and add the implicit_packages line it prints ...')
```

A torch build chosen by rez alone stops the environment with that message, so `rez env` (and the
worker) fails instead of running the wrong build. A machine's own implicit package, or an explicit
`.torch_backend-<build>` in the request, satisfies it.

Packages without torch builds, and torch on macOS, are unaffected. `tests/integration` checks all
of this against real rez.

The guard reads the build from the resolved variant's own requirements, not rez's `ephemerals`
object. Rez 3.4.0 builds that object once and caches it. When a package has a late-bound
`requires()`, as the per-platform pip packages do, it is built before the ephemerals are known.
`commands()` would see it empty and the guard would never fire.

### Can I set the build up front instead?

Deploy one rezconfig per GPU class the way the studio deploys other per-host configuration:
`.torch_backend-cu128` on RTX hosts, `.torch_backend-cpu` on CPU-only farm nodes.

## Can several platforms share one store?

Yes, a store can be built into from several platforms.

- Pure-Python packages have one variant for every platform. Their platform-specific dependencies
  are chosen when rez resolves them.
- Compiled packages get one variant per platform, arch, Python, and torch build. A build adds this
  machine's variant without touching the others.
- Engine and library packages carry no variants and are kept when they already exist, so a
  second platform's build only adds its variants.

### What happens when a build fails?

A build whose final rez check fails removes what that run wrote and lists it in the error: the
versions it created, and variants it added to other builds' versions. A plain rerun then
builds them again instead of reusing packages that do not resolve.

Packages other builds wrote are never touched. When everything involved was already in the store,
the error says to pass `--rebuild`.

With `--rebuild`, a replaced engine, library, or launch package version is kept aside, in a folder
rez ignores, until the check passes. It is put back if the check fails, so a failed rebuild leaves
artists the version that worked.

Nothing is ever half-written. Payloads are staged next to their destination and renamed into
place. `package.py` is written last, itself through a temporary file and a rename.

## How are pip versions converted to rez?

PEP 440 specifiers become rez ranges the way rez-pip converts them. Bounds are tightened so
that `1`, `1.0`, and `1.0.0` (one version to pip, three to rez) land on the same side.

| PEP 440 | rez |
| :-- | :-- |
| `>=1.26,<3` | `1.26+<3` |
| `<=2` | `<2.0.0.1` |
| `>1.0` | `1.0.0.1+` |
| `!=1.5` | `<1.5\|1.5.0.1+` |
| `~=1.4.2` | `1.4.2+<1.5` |
| `==1.2.*` | `1.2+<1.3` |

A specifier with no rez equivalent (`===`, a malformed one, one that excludes every version)
fails the build instead of meaning any version. The full table is in
`src/griptape_nodes_rez_build/versions.py`.

## Development

```bash
make install      # install the tool and its dev dependencies
make check        # format, lint, type and spelling checks
make test/unit    # unit tests
```

`make test/integration` runs the tests that need rez. Point `GRIPTAPE_NODES_REZ_TOOLS` at rez's tools
first. The code rules for contributors are in `CLAUDE.md`, which AI coding agents also read.
