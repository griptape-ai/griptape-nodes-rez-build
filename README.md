# griptape-nodes-rez-build

Builds [Griptape Nodes](https://docs.griptapenodes.com) as [rez](https://rez.readthedocs.io)
packages, so a studio can run it the way it runs its other tools:

```bash
rez env griptape_launch -- gtn
```

The command `griptape-nodes-rez` writes four kinds of packages into a package store:

| Command | Writes |
| :-- | :-- |
| `build-engine` | `griptape_nodes_engine` (the engine), `griptape_nodes` (the `gtn` app), `griptape_nodes_editor_dist` (the editor), and every pip dependency |
| `build-library` | `<family>_edit` and `<family>` for a node library, plus the libraries it declares and every pip dependency |
| `write-launch` | `griptape_launch`, which artists start Griptape Nodes with |
| `gpu-config` | nothing: prints the rezconfig line that picks this machine's torch build |

The engine contains no rez code. It reads what these packages set: `GTN_LIBRARY_PATHS`,
`GTN_LIBRARY_WORKER_REQUESTS`, `GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE`, and
`GTN_CONFIG_WORKER__COMMAND_PREFIX`.

## Requirements

- rez 3 (tested with 3.4.0), for its tools (`rez`, `rez-env`, `rez-config`, `rez-search`). Pass the folder with
  `--rez-tools`, set `GRIPTAPE_NODES_REZ_TOOLS`, or put `rez-env` on `PATH`. In a standard rez
  install the folder is `<prefix>/bin/rez`.
- The `platform`, `arch`, `os`, and `python` packages `rez-bind` creates, for the Python version
  the tool runs with. Every build is resolved with rez before it counts as done, so a build stops
  when they are missing.
- Python 3.12 or 3.13. Packages are built for the Python running the tool, so run it with the
  Python your `python` rez package provides (`uv run --python 3.12 ...`).
- git, for `build-library --git-url` and declared library dependencies.

uv comes with the tool. `UV_INDEX_URL`, `UV_FIND_LINKS`, `UV_OVERRIDE`, and other uv settings in
the environment apply to every resolve unchanged. `UV_TORCH_BACKEND` is refused (see
[Torch builds](#torch-builds)).

The tool never changes rez configuration. It runs rez with the environment it was started in,
and points its checks at the store being built with `rez env --paths <store>:<packages_path>`,
so the store does not need to be on your search path while you build.

## Install

```bash
uv tool install git+https://github.com/griptape-ai/griptape-nodes-rez-build
griptape-nodes-rez --help
```

or, from a checkout, `uv run --project <checkout> griptape-nodes-rez ...`.

## Commands

Options every command takes: `--rez-tools PATH`. The top-level `--verbose` shows each package as
it is resolved and written (`griptape-nodes-rez --verbose build-engine ...`).

### build-engine

```bash
griptape-nodes-rez build-engine --engine-repo PATH --local-packages-path PATH \
    [--no-editor] [--app-version V] [--rebuild] [--yes]
```

Packages an engine checkout (read as files, never imported) with the `gtn` app and the prebuilt
editor. The engine package's `requires` are the version ranges from the checkout's
`pyproject.toml` (and `requires-python`), not the versions this build resolved, so the engine
resolves together with node libraries wherever their ranges overlap. Reproducibility comes from
released package sets and `rez env --time`.

The app and editor are pinned to the versions this build installed (`griptape_nodes-0.99.0`).
The app is released against specific engine versions and declares an exact engine requirement;
the build replaces that with the checkout (`griptape-nodes-engine @ file://...`), so rez must not
swap in a different app later.

- `--no-editor` leaves out `griptape_nodes_editor_dist`.
- `--app-version V` picks the app version; otherwise uv picks the newest.
- An existing engine package of the same version is kept, and the build only adds what this
  machine needs (its variants of compiled dependencies). `--rebuild` replaces the engine package
  and reinstalls this machine's variants of every dependency.

The check: `rez env griptape_nodes_engine==<version>` resolves from the store and
`griptape_nodes`, `griptape_nodes_app`, the editor, and `gtn` all come from it.

### build-library

```bash
griptape-nodes-rez build-library (--git-url URL [--branch REF] | --local-path PATH) \
    --local-packages-path PATH [--torch-backend B ...] [--rebuild] [--yes]
```

Packages a node library as two packages, named after the repository or folder it was built from
(`griptape-nodes-library-standard` -> `griptape_nodes_library_standard`), never its display name:

- `<family>_edit`: the library's source and manifest under `python/<repository or folder name>/`
  (the engine recognises a declared library dependency by that folder name in the manifest's
  path), requiring its `pip_dependencies` and the
  `_edit` packages of the libraries it declares (`library_dependency`; weak `~<family>_edit` for
  `required: false`). Its `commands()` publish the library to the engine:

  ```python
  env.PYTHONPATH.append("{root}/python/griptape-nodes-library-rez-test")
  env.GTN_LIBRARY_PATHS.append("{root}/python/griptape-nodes-library-rez-test/griptape_nodes_library.json")
  env.GTN_LIBRARY_WORKER_REQUESTS.append(literal("Rez Test Library=griptape_nodes_library_rez_test==0.1.0"))
  ```

- `<family>`: requires `<family>_edit==<version>` and the `pip_dependencies_exec`. A worker for the
  library runs in `rez env griptape_nodes_engine==<engine> <family>==<version>`. A library with no
  execution dependencies still gets this package, so every worker request looks the same.

Both use the manifest's version ranges. The version comes from `pyproject.toml` next to the
manifest (or up to four folders above), then `git describe --tags`, then the manifest's
`library_version`, then `1.0.0`. Declared library dependencies are cloned and built first when
the store has no `_edit` package for them, or always with `--rebuild` (which also rebuilds their
dependencies). Existing packages of the same version are kept unless `--rebuild`.

The check: both packages resolve from the store, and `GTN_LIBRARY_PATHS` and
`GTN_LIBRARY_WORKER_REQUESTS` hold this library's manifest and worker request.

### write-launch

```bash
griptape-nodes-rez write-launch --tools PLATFORM=PATH ... --library FAMILY[-VERSION] ... \
    --local-packages-path PATH [--testing-store PLATFORM=PATH] [--config-file PLATFORM=PATH] \
    [--set NAME=VALUE ...] [--engine-version V] [--version V] [--print] [--rebuild] [--yes]
```

Writes `griptape_launch`, requiring `griptape_nodes_engine==<engine>` and the chosen libraries'
`<family>_edit==<version>` (the newest in the store or on rez's search path when no version is
given). Without `--library` the package provides the engine only, and the command warns that no
libraries will load. Its `commands()` pick the settings for `system.platform` and stop on a platform that was
not given with `--tools`:

- `GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE=environment`
- `GTN_CONFIG_WORKER__COMMAND_PREFIX=["<tools>/rez", "env", "--norc", "griptape_nodes_engine=={engine_version}", "{library_request}", "--"]`,
  written with `literal()` so rez leaves the placeholders for the engine to fill
- `--set` values on every platform (`XDG_CONFIG_HOME`, for example)
- `--config-file`: appended to `REZ_CONFIG_FILE`, after the studio's configuration, so every
  worker's `rez env` reads it too. Rez replaces an inherited variable the first time a package
  appends to it (unless the studio lists it in `parent_variables`), so the package reads the
  studio's value with rez's `getenv()` and sets it back before appending; no rez configuration
  change is needed, and `append()` uses each platform's own separator. For a testing launch package, this is how rez finds a local
  store: give a rezconfig with `packages_path = ModifyList(prepend=["<store>"])`.
- `--testing-store` (testing only): recorded in the package as `testing_stores` and sets
  nothing. The engine reads no rez settings, so rez itself has to find the store (see
  `--config-file`).

`--tools` paths must be absolute on their platform; `PLATFORM` is `linux`, `osx`, or `windows`.
`--print` shows the package instead of writing it. `--version` defaults to one patch above the
newest launch package in the store; an existing version is replaced only with `--rebuild`.

The check (on a platform the package is set up for): `rez env griptape_launch==<version>`
resolves from rez's configuration, the engine and app come from the stores, the settings above
are set, and, from inside that environment, each library's worker is started through the prefix
the way the engine starts it. A store rez does not search yet is added for the check with
`REZ_PACKAGES_PATH`. When the launch package does not resolve, each library is resolved with the
engine, then in pairs, and the message names the libraries that conflict and on what package. A
launch package that fails its check is removed from the store.

### gpu-config

```bash
griptape-nodes-rez gpu-config [--local-packages-path PATH ...]
```

Prints the rezconfig line for this machine, from `nvidia-smi` and the torch builds in the store
(or on rez's search path):

```python
# Torch build for render-042 (linux-x86_64): NVIDIA driver supports CUDA 12.8; builds in the store: cpu, cu118, cu128.
# Add to this machine's rezconfig (never to one shared by machines with different GPUs):
implicit_packages = ModifyList(append=[".torch_backend-cu128"])
```

It picks the newest CUDA build the driver supports, or `cpu` without an NVIDIA GPU. On macOS
there is a single torch build and nothing to configure.

## Torch builds

On Linux and Windows, `build-library --torch-backend cu128 --torch-backend cpu` (or `all`) installs
torch once per build. Each becomes a variant carrying the ephemeral `.torch_backend-<build>`,
so one store serves every GPU generation. Each machine chooses its build with
`implicit_packages`, the approach the rez documentation describes for GPU packages (*Ephemeral
Packages*); `gpu-config` prints the line.

`--torch-backend=auto` in a manifest's `pip_install_flags`, and `UV_TORCH_BACKEND` in the shell,
are refused: they make uv pick torch for the GPU of the machine running the build, which would put
that machine's build into a store every workstation shares. With `--torch-backend`, the
manifest's torch source is replaced by each chosen build. On macOS `--torch-backend` is ignored.

### The guard

Rez satisfies an ephemeral in a variant just by resolving it, so on a machine whose rezconfig
does not name a build, rez would quietly pick one of the CUDA variants. Every package with a
variant per torch build (torch, torchvision, ...) therefore checks in its `commands()`:

```python
if 'torch_backend' in ephemerals and '.torch_backend' not in implicits and '.torch_backend' not in request:
    stop(... 'run "griptape-nodes-rez gpu-config" and add the implicit_packages line it prints ...')
```

A torch build chosen by rez alone stops the environment with that message, so `rez env` (and
the worker) fails instead of running the wrong build. A machine's own implicit package, or an
explicit `.torch_backend-<build>` in the request, satisfies it. Packages without torch builds,
and torch on macOS, are unaffected. `tests/integration` checks all of this against real rez.

The alternative is to set the implicit on every machine up front: one rezconfig per GPU class
(`.torch_backend-cu128` on RTX hosts, `.torch_backend-cpu` on CPU-only farm nodes), deployed the
way the studio deploys other per-host configuration.

## Shared stores and platforms

A store can be built into from several platforms. Pure-Python packages have one variant for
every platform, with platform-specific dependencies chosen when rez resolves them. Compiled
packages get one variant per platform, arch, Python, and torch build, and a build adds this
machine's variant without touching the others. Engine and library packages carry no variants
and are kept when they already exist, so a second platform's build only adds its variants.

A build whose final rez check fails removes what that run wrote (package versions it created,
and variants it added to versions other builds created) and lists them in the error, so a plain
rerun builds them again instead of reusing packages that do not resolve. Packages other builds
wrote are never touched. When everything involved was already in the store, the error says to
pass `--rebuild`.

Nothing is ever half-written: payloads are staged next to their destination, renamed into
place, and `package.py` is written last, itself through a temporary file and a rename.

## Version ranges

PEP 440 specifiers become rez ranges the way rez-pip converts them, with bounds tightened so
that `1`, `1.0`, and `1.0.0` (one version to pip, three to rez) land on the same side:

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

See `CLAUDE.md`. `make install && make check && make test/unit`; `make test/integration` runs the
tests that need rez (`GRIPTAPE_NODES_REZ_TOOLS`).
