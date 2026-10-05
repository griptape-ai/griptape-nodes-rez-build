---
name: griptape-nodes-rez-build
description: >-
  Running Griptape Nodes under Rez with griptape-nodes-rez-build: the engine has no
  Rez code and reads library.provisioned_by = "environment",
  worker.command_prefix, GTN_LIBRARY_PATHS and GTN_LIBRARY_WORKER_REQUESTS; Rez
  resolves the engine and every library's edit-time package before gtn starts; the
  griptape-nodes-rez command (build-engine / build-library / write-launch /
  gpu-config) writes the engine package with version ranges, <family>_edit and
  <family> library packages, the griptape_launch package, and prints the
  per-machine .torch_backend implicit that the torch packages' guard requires;
  optional sandbox opt-in (library.sandbox_enabled,
  write-launch --allow-sandbox, Refresh Sandbox). Use when setting up, debugging,
  or extending a Rez deployment of Griptape Nodes, or working on this repository.
license: Apache-2.0
metadata:
  project: griptape-nodes
  source: https://github.com/griptape-ai/griptape-nodes-rez-build
---

# Griptape Nodes under Rez: griptape-nodes-rez-build

This repository's `README.md` is the command reference and `docs/` holds the administrator guides.
The engine side is described in the engine repository's user guide,
`docs/guides/managed_environment.md` (*Running in a Managed Environment*). Engine file paths below
are relative to the engine repository; everything else is relative to this one.

## The model in one paragraph

Rez builds the whole editor environment **before** the engine starts:
`rez env --norc griptape_launch -- gtn`. `griptape_launch` requires `griptape_nodes_engine==<v>`
(which requires the `gtn` app and the editor at exact versions) and `<family>_edit==<v>` per library.
Each `_edit` package's `commands()` put the library on `PYTHONPATH` and publish it in
`GTN_LIBRARY_PATHS` and `GTN_LIBRARY_WORKER_REQUESTS`. The launch package sets
`library.provisioned_by = "environment"` and `worker.command_prefix`. The engine then builds no
venvs, downloads and updates nothing, makes no Rez calls, and loads only libraries in
`GTN_LIBRARY_PATHS`. Each library's worker starts as
`<rez> env --norc griptape_nodes_engine==<v> python-<engine python> <family>==<v> -- <absolute python> -m griptape_nodes_app engine ...`,
so heavy execution dependencies (torch) stay out of the editor. Nothing in the engine is
Rez-specific.

## Engine hooks

Settings in `src/griptape_nodes/retained_mode/managers/settings.py`; hooks in `library_manager.py`
and `worker_manager.py`. User-facing description: `docs/guides/managed_environment.md`.

| Name | Kind | Values | Override |
|:--|:--|:--|:--|
| `library.provisioned_by` | setting | `"engine"` (default), `"environment"` | `GTN_CONFIG_LIBRARY__PROVISIONED_BY` |
| `worker.command_prefix` | setting | list of strings, default `[]` | `GTN_CONFIG_WORKER__COMMAND_PREFIX` (JSON list) |
| `library.sandbox_enabled` | setting | `true`, `false`, unset (default: on when the engine provisions, off in environment mode) | `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED` |
| `GTN_LIBRARY_PATHS` | env var | `os.pathsep`-separated manifest paths or folders to search | |
| `GTN_LIBRARY_WORKER_REQUESTS` | env var | `os.pathsep`-separated `<manifest name>=<request>` | |

- `GTN_LIBRARY_PATHS` applies in **both** modes: entries register like `libraries_to_register`
  entries, before them. Relative paths are relative to the workspace.
- `"environment"` mode refuses everything else: other configured libraries and the Sandbox Library
  are listed as "not loaded because the engine is running in an environment that provides its
  libraries"; `libraries_to_download` is ignored; download/update/switch/sync requests fail saying
  the environment manages libraries; update checks report no update ("provided by the
  environment") without contacting git; the sandbox is not scanned and sandbox nodes from a file
  are refused (unless `sandbox_enabled` is true, below); no `.venv` or `.venv-exec` is created or
  reused; the user's config file is not pruned. A misspelled value is logged and ignored.
- `library_dependency` in environment mode is satisfied only by a library in `GTN_LIBRARY_PATHS`,
  matched by the dependency's repository name against the folders in each loaded manifest's path
  (case-insensitive, `-` and `_` alike). Packages from the build tool keep the source in
  `python/<repo or folder name>/` for this.
- `library.sandbox_enabled` applies in both modes. Unset: on when the engine provisions libraries,
  off in environment mode; `false` turns it off when the engine provisions too. Any letter case;
  other values are logged and ignored, leaving the default. Set to `true` in environment mode: the
  Sandbox Library
  (`sandbox_library_directory`) is scanned and loaded in the orchestrator, sandbox nodes can be
  added (`RegisterSandboxNodeFromSourceRequest`), and it is listed in library metadata. No venv is
  built for it: it runs in the orchestrator, whose environment is the engine plus each library's
  edit-time packages (`pip_dependencies` via `<family>_edit`). `pip_dependencies_exec` (torch, ...)
  exist only in that library's worker, so sandbox nodes cannot import them. To add a package: make
  it an edit-time dependency of an included library, or include it in the launch request.
  Everything else stays refused.
- `ReloadSandboxLibraryRequest` (success/failure results) reloads only the sandbox: environment
  libraries stay loaded and their workers keep running. Nodes already in a workflow keep their old
  class until recreated. With the sandbox off it fails with an artist-readable reason.
- Editor in environment mode: every "Refresh Libraries" entry point is disabled with the tooltip
  "Libraries come from the environment the engine was started in. Relaunch the engine to pick up
  library changes." With the sandbox opt-in the button reads "Refresh Sandbox" and keeps the
  Refresh Libraries flow: save prompt for unsaved work, `ReloadSandboxLibraryRequest`, then reopen
  the saved workflow, which recreates its nodes with the new sandbox code. The Sandbox Settings pane
  is shown.
- The prefix goes in front of the absolute interpreter path
  (`sys.executable -m griptape_nodes_app engine ...`). Placeholders: `{library_request}` (a word that
  is exactly it expands to one word per space-separated part), `{library_name}`, `{engine_version}`,
  `{python_version}` (engine's Python, major.minor). The prefix cannot change the interpreter, so the
  prepared environment must be for the engine's Python; the launch package requests
  `python-{python_version}` for this.
- A library with no request entry: `"environment"` mode does not start its worker (nodes stay
  editable; running one says the environment does not say which packages its worker needs); never
  runs unprefixed. When the engine provisions libraries (`"engine"`), it runs without the prefix.
- The worker gets the engine's startup environment plus `GTN_ENGINE_ID`,
  `GTN_ORCHESTRATOR_ENGINE_ID`, `PYTHONUNBUFFERED`, and the static server address; the engine copies
  nothing else and changes nothing, so the prefix tool decides the rest.
- No GPU detection in the engine.

## Build tool

Python package `griptape_nodes_rez_build` (`src/`), command `griptape-nodes-rez`. Install with
`uv tool install git+https://github.com/griptape-ai/griptape-nodes-rez-build` or run
`uv run --python 3.12 --project <checkout> griptape-nodes-rez ...`. Needs Rez 3 tools
(`--rez-tools PATH` on every command, or `GRIPTAPE_NODES_REZ_TOOLS`, or `rez-env` on `PATH`), the
`platform`/`arch`/`os`/`python` bind packages for the Python it runs with, and git. Top-level
`--verbose` shows each package as it is resolved and written. It never changes Rez configuration;
its checks use `rez env --paths <store>:<packages_path>`.

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

- **`build-engine`**: the engine is the published `griptape-nodes-engine` by default (newest the
  app accepts, chosen by resolving the two together with uv, or `--engine-version`), unpacked with
  `uv pip install --no-deps --target`; `--engine-repo` builds a checkout instead (read as files,
  never imported) for studios changing the engine. `griptape_nodes_engine`'s `requires` are the
  engine's declared ranges (release `Requires-Dist` minus extras, or the checkout's `pyproject.toml`)
  and `requires-python`; `griptape_nodes` (app) and `griptape_nodes_editor_dist` pinned to the
  versions installed (the app accepts an engine range; the resolve meets it with the engine being
  packaged); every pip dependency. Existing engine version kept, only this
  machine's variants added; `--rebuild` replaces. Check: `rez env griptape_nodes_engine==<v>`
  provides engine, app, editor, `gtn`.
- **`build-library`**: `<family>` = repo or folder name, lowercase, underscores.
  - `<family>_edit`: source and manifest under `python/<repo or folder name>/`; requires
    `pip_dependencies` (manifest ranges) and unversioned `<dep>_edit` for each `library_dependency`
    (weak `~<dep>_edit` for `required: false`); `commands()`:
    `env.PYTHONPATH.append("{root}/python/<name>")`,
    `env.GTN_LIBRARY_PATHS.append("{root}/python/<name>/<manifest>")`,
    `env.GTN_LIBRARY_WORKER_REQUESTS.append(literal("<manifest name>=<family>==<version>"))`.
  - `<family>`: requires `<family>_edit==<version>` plus `pip_dependencies_exec`; always written, so
    worker requests are uniform.
  - Version: `pyproject.toml` next to the manifest (or up to four folders up), `git describe --tags`,
    manifest `library_version`, `1.0.0`. Declared library dependencies are cloned and built first
    unless their `_edit` package is in the store (`--rebuild` rebuilds them too). No lock pins.
- **`write-launch`**: `griptape_launch` requiring `griptape_nodes_engine==<v>` and
  `<family>_edit==<v>` (newest in store or on the search path when unversioned); zero `--library`
  writes an engine-only package with a warning. `commands()` per `system.platform` (stop on an
  unconfigured one): `GTN_CONFIG_LIBRARY__PROVISIONED_BY=environment`,
  `GTN_CONFIG_WORKER__COMMAND_PREFIX=["<tools>/rez", "env", "--norc",
  "griptape_nodes_engine=={engine_version}", "python-{python_version}", "{library_request}", "--"]` via `literal()`, `--set`
  values on every platform, `--config-file` appended to `REZ_CONFIG_FILE` after the studio's value
  (how a testing setup lets workers find a local store: a rezconfig with
  `packages_path = ModifyList(prepend=[...])`). `--allow-sandbox` (off by default) adds
  `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED=true` on every platform. `--testing-store` is only
  recorded as `testing_stores` and sets nothing; the tool warns if it is given without a config
  file. `--tools` paths absolute; `--version` defaults to newest + 1 patch; replacing needs
  `--rebuild`. The check resolves the package and each library's worker request, and names every
  library (or pair of libraries) that cannot resolve together.
- **`gpu-config`**: prints, from `nvidia-smi` and the builds in the stores (or the search path):
  a comment line naming the host, driver CUDA and available builds, then
  `implicit_packages = ModifyList(append=[".torch_backend-cu128"])` (newest supported CUDA build, or
  `cpu`). Nothing to configure on macOS.
- Guarantees: PEP 440 to Rez conversion as `rez-pip` does with tightened bounds (`<=2` ->
  `<2.0.0.1`, `>1.0` -> `1.0.0.1+`, `!=1.5` -> `<1.5|1.5.0.1+`; a bound on a pre-release uses its
  release), unconvertible specifiers fail the build (table in `versions.py`); payloads staged and
  renamed, `package.py` last via temp file and rename; builds are resolved before they count as
  done, and a build whose final check fails removes what it wrote (a version replaced with
  `--rebuild` is kept aside until the check passes and put back on failure). Rez checks run from script files,
  never `python -c` (Windows `rez-env` strips double quotes).

## Torch builds and the guard

- `--torch-backend B` (repeatable, or `all`) on Linux/Windows: one variant per build carrying
  `.torch_backend-<build>`; ignored on macOS. `--torch-backend=auto` in `pip_install_flags` and
  `UV_TORCH_BACKEND` are refused (they would bake the build machine's GPU into a shared store).
- Each machine picks its build with `implicit_packages` (the Rez docs' *Ephemerals* GPU example).
- Guard: every package with per-build torch variants has in `commands()`:

  ```python
  builds = [str(r) for r in this.variant_requires if str(r).startswith(".torch_backend-")]
  if builds and ".torch_backend" not in implicits and ".torch_backend" not in request:
      stop(...)
  ```

  telling the admin to run `griptape-nodes-rez gpu-config` and add the printed line to that
  machine's rezconfig. So a build Rez picked alone stops `rez env` (and the worker). An implicit or
  an explicit `.torch_backend-<build>` request satisfies it. It reads `this.variant_requires`, not
  `ephemerals`: Rez 3.4.0 caches `ephemerals` empty when any package in the resolve has a late-bound
  `requires()`, so an `ephemerals`-based guard never fires. Packages built before this carry the old
  guard; rebuild with `--rebuild`. Alternative: deploy one rezconfig per GPU class up front (`cpu` on
  CPU-only nodes).

## Choosing environments

`GTN_LIBRARY_PATHS` works with any way of building the request: the launch package (the one the
build tool writes), a launcher profile (kloch `rezenv: requires:`, toolbox `rez_wants`), or a
project config fixing full versions. Without the launch package, the engine settings come from
launcher environment variables or the project's `griptape_nodes_config.json`, where the prefix uses
plain `rez` (on `PATH` in a resolved context by default, `rez_tools_visibility = "append"`). The
torch guard works in all cases. See `docs/choosing-environments.md`.

## Troubleshooting

| Symptom | Likely cause |
|:--|:--|
| `rez env griptape_launch` fails naming two packages | Two libraries' edit-time deps conflict. Split into launch packages per show or task. |
| Library listed as not loaded, "environment that provides its libraries" | It is configured but no `_edit` package in the request publishes it. |
| Running a node: environment does not say which packages its worker needs | No `GTN_LIBRARY_WORKER_REQUESTS` entry, or its name differs from the manifest `name`. |
| Library reports a missing library dependency that is present | The dependency's manifest path lacks a folder named after its repository (non-build-tool environment). |
| Worker's `rez env` stops asking to run `gpu-config` | The torch guard: no `.torch_backend-*` implicit on this machine. Add the printed line to its rezconfig. |
| Wrong torch build runs, no guard message | Packages built with the old `ephemerals` guard; rebuild them with `--rebuild`. |
| Worker cannot find packages from the local store | The testing launch package has no `--config-file` rezconfig that prepends the store (`--testing-store` alone sets nothing). |
| Worker's `rez env` fails naming `python-3.x` | The library's execution packages do not support the engine's Python; the worker must run on the engine's interpreter. Build the library for that Python, or start the editor on a Python the library supports. |
| Worker imports a different engine or `mcp` than the editor | The prefix lacks `griptape_nodes_engine=={engine_version}`, so Rez replaced `PYTHONPATH` without the engine. |
| A local `gtn` runs instead of the environment's | Shell rc files reordered `PATH`; start with `rez env --norc`. cmd/PowerShell ignore `--norc`. |
| Sandbox refused in environment mode | Expected unless the launch package was written with `--allow-sandbox` (or the setting is set). |
| Sandbox node fails with an import error | No venv for the sandbox; the package must be an edit-time dependency of an included library or in the launch request. Exec-only packages (torch) are worker-only. |
| "Refresh Libraries" greyed out | Expected in environment mode; relaunch for library changes. With the opt-in it reads "Refresh Sandbox". |
| `ImportError` in the editor but not in the worker | The module belongs in `pip_dependencies`, not `pip_dependencies_exec`. |

## Docs

- `README.md`: command reference, torch builds, shared stores, version ranges.
- `docs/overview.md` and the pages it links: deployment, configuration, libraries, GPU builds,
  choosing environments, releasing (with `examples/release_family.sh`).
- `CLAUDE.md`: development commands and repository rules.
