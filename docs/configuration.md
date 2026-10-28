# Configuration

The engine reads two settings and two environment variables, plus an optional setting that lets
artists keep a sandbox library. None of them mention Rez. They say "the environment provides the
libraries" and "start workers with this command in front".

In a Rez setup, the launch package sets the engine settings. The two environment variables come
from each library's `_edit` package, which the launch package requires.

Anything else that can set environment variables can provide them too. The engine's guide,
*Running in a Managed Environment* (`docs/guides/managed_environment.md` in the engine
repository), describes them for any tool. This page describes them as Rez uses them.

## What the engine reads

| Name | Kind | Needed | Effect |
|:--|:--|:--|:--|
| `library.provisioned_by` | engine setting | yes, `"environment"` | Only environment libraries load. No venvs, downloads, or updates |
| `worker.command_prefix` | engine setting | for libraries with workers | Command placed in front of each worker's interpreter |
| `library.sandbox_enabled` | engine setting | optional | `true` lets the Sandbox Library load in environment mode. Nothing else |
| `GTN_LIBRARY_PATHS` | environment variable | yes | Which libraries load (first, in either mode) |
| `GTN_LIBRARY_WORKER_REQUESTS` | environment variable | for libraries with workers | Each library's worker request |
| `REZ_CONFIG_FILE` (your studio's) | environment variable | as today | Used as-is. A launch package's `--config-file` only appends to it |

## Engine settings

| Setting | Values | Environment override |
|:--|:--|:--|
| `library.provisioned_by` | `"engine"` (default) or `"environment"` | `GTN_CONFIG_LIBRARY__PROVISIONED_BY` |
| `worker.command_prefix` | A list of strings, default `[]` | `GTN_CONFIG_WORKER__COMMAND_PREFIX`, as a JSON list |
| `library.sandbox_enabled` | `true`, `false`, or unset | `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED` |

Like other engine settings, these can live in `griptape_nodes_config.json` or come from the
`GTN_CONFIG_*` overrides. A launch package uses the overrides.

### `library.provisioned_by`

This says who provisions the libraries and their Python packages.

- `"engine"`: the engine behaves as it always has. It builds a virtual environment for each library
  and downloads libraries it is asked for.
- `"environment"`: the environment the engine was started in provides them.

With `"environment"`, the engine loads only what the environment lists:

- Only the libraries in `GTN_LIBRARY_PATHS` load.
- Every other configured library is listed with a problem, including `libraries_to_register`
  entries and the Sandbox Library. It reads: "This library was not loaded
  because the engine is running in an environment that provides its libraries, and this library is
  not one of them."
- A library's `library_dependency` is satisfied only by a library in `GTN_LIBRARY_PATHS`, never by a
  download. See [Libraries](libraries.md#library-dependencies) for how the engine matches them.
- The Sandbox Library is neither scanned nor loaded. Adding a sandbox node from a file fails with a
  message saying the environment manages libraries. Both change if the studio enables the sandbox
  (see [Allowing a sandbox library](#allowing-a-sandbox-library)).

It also downloads and builds nothing:

- Nothing is downloaded, updated, or synced. `libraries_to_download` is ignored.
- Downloading, updating, switching, or syncing a library from the editor fails with a message saying
  the environment manages libraries.
- Checking a library for updates reports no update and says updates come from the environment. It
  does not contact the library's git remote.
- No `.venv` or `.venv-exec` folder is created, and none left from an earlier run is used.
- The artist's own config file is left alone. Entries for libraries that did not load are not
  removed.

In the editor:

- Library settings are read-only, and each library shows where it came from.
- The Sandbox Settings pane is hidden, unless a sandbox is allowed.
- **Refresh Libraries** is greyed out everywhere it appears. Its tooltip says libraries come from
  the environment the engine was started in, and a relaunch picks up library changes.

A misspelled value is reported in the engine log and ignored, not quietly treated as `"engine"`. A
dependency the environment is missing shows up as an ordinary import error for that library.

### Allowing a sandbox library

Set `library.sandbox_enabled` to `true` to let artists develop their own nodes in environment mode.
It lets in one more library, the Sandbox Library: the folder in
**Settings → Library → Sandbox Settings**.

- With `write-launch --allow-sandbox`, the launch package sets
  `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED=true` on every platform.
- Without a launch package, set the same variable in the launcher profile. Or set
  `"library": {"sandbox_enabled": true}` in the project's `griptape_nodes_config.json`. See
  [Choosing environments](choosing-environments.md).

With the sandbox on, the Sandbox Library is scanned and loaded **in the editor's own process**, and
artists can add sandbox nodes. Every other library the environment does not
list is still refused, and nothing is downloaded or installed.

In the editor, **Refresh Libraries** becomes **Refresh Sandbox**. It reloads only the sandbox, so new
or changed node files show up without relaunching. The environment's libraries stay loaded and their
workers keep running.

Like Refresh Libraries, it asks to save a workflow with unsaved changes, then reopens the workflow so
its sandbox nodes use the new code. Changes to the environment's own libraries still need a relaunch.

#### What sandbox nodes can import

- No virtual environment is built for the sandbox. Sandbox nodes run in the editor's process.
- That process runs in the launch environment: the engine plus each library's **edit-time**
  packages. Those are the library's `pip_dependencies`, brought in by `<family>_edit`.
- A library's execution-only packages (`pip_dependencies_exec`, such as torch) exist only in that
  library's worker. Sandbox nodes cannot import them.
- A node that imports something missing reports the import error.

To make a package available to sandbox nodes, put it in the launch environment in one of two ways:

- As an edit-time dependency (`pip_dependencies`) of a library the environment includes.
- As a package the launch request itself includes. A launcher profile or project config can request
  any package.

#### The setting in either mode

- Left unset, the sandbox is on when the engine provisions libraries and off when the environment
  does.
- `false` turns it off even when the engine provisions libraries.
- It accepts `true` or `false` in any letter case.
- Any other value from the environment variable is reported in the engine log and ignored. The
  default stays in place, so environment mode keeps the sandbox refused.

### `worker.command_prefix`

A library whose nodes need their own dependencies runs them in a separate worker process, started
with the engine's Python interpreter. The prefix goes in front of that command, so the worker starts
inside whatever environment the prefix creates:

```bash
<absolute path to python> -m griptape_nodes_app engine --library-name "..." ...
```

Placeholders in the prefix are filled for each worker:

| Placeholder | Filled with |
|:--|:--|
| `{library_request}` | The library's entry in `GTN_LIBRARY_WORKER_REQUESTS` |
| `{library_name}` | The library's name, as in its manifest |
| `{engine_version}` | The running engine's version, such as `0.103.0` |
| `{python_version}` | The Python the engine runs on, as major.minor, such as `3.12` |

A word that is exactly `{library_request}` becomes one word per space-separated part of the entry,
so one entry can name several packages. Inside a longer word it is replaced as text.

The launch package sets, per platform, with that platform's `--tools` folder plus `/rez`:

```json
["/mnt/pipeline/rez/bin/rez/rez", "env", "--norc", "griptape_nodes_engine=={engine_version}", "python-{python_version}", "{library_request}", "--"]
```

So a worker for `lib_foo` 1.4.2 starts as:

```bash
/mnt/pipeline/rez/bin/rez/rez env --norc griptape_nodes_engine==0.103.0 python-3.12 lib_foo==1.4.2 -- /path/to/python -m griptape_nodes_app engine ...
```

The interpreter path is the `python` package the editor's environment resolved.

#### What each worker gets

- The same Python. The worker always runs the editor's interpreter, so `python-{python_version}`
  makes its resolve pick packages for that same Python minor version. A library that cannot run on
  it fails the worker's `rez env` with Rez's own message. It does not load packages built for
  another Python.
- Exact versions. The worker resolves the same engine and library versions the editor loaded. A
  newer library in the store does not change a running environment's workers.
- The engine's environment, as it was when the engine started. The engine adds a few variables by
  name: `GTN_ENGINE_ID`, `GTN_ORCHESTRATOR_ENGINE_ID`, `PYTHONUNBUFFERED`,
  and the static file server address. It changes nothing else, so `rez env` decides what the
  worker's environment finally contains.
- Torch builds, picked by Rez inside the worker's resolve from the machine's implicit packages. See
  [GPU builds per machine](gpu-builds.md).

Keep the prefix free of quoted arguments, because some Windows shells drop double quotes inside
them.

#### A library with no request

With `"environment"`, a library that has no entry in `GTN_LIBRARY_WORKER_REQUESTS` does not start a
worker. Its nodes stay editable. Running one says the environment does not say which packages its
worker needs. It never runs without the prefix.

When the engine provisions libraries (`"engine"`), such a library runs without a prefix, as if none
were configured.

## Environment variables the engine reads

Both are lists separated by the platform's path separator: `:` on macOS and Linux, `;` on
Windows. Rez's `env.<VAR>.append(...)` joins values with that separator by default
([Package Commands](https://rez.readthedocs.io/en/stable/package_commands.html)).

| Variable | Entries | Set by |
|:--|:--|:--|
| `GTN_LIBRARY_PATHS` | Library manifests (`griptape_nodes_library.json`), or folders to search for them | Each `<family>_edit` package |
| `GTN_LIBRARY_WORKER_REQUESTS` | `<library name>=<request>`, where `<library name>` is the manifest's `name` | Each `<family>_edit` package |

`GTN_LIBRARY_PATHS` works with either dependency source. Its libraries load before the
`libraries_to_register` entries. Only `"environment"` limits loading to this list. A relative path
is read relative to the workspace.

A `<family>_edit` package's `commands()`, as the build tool writes them:

```python
def commands():
    env.PYTHONPATH.append("{root}/python/griptape-nodes-library-standard")
    env.GTN_LIBRARY_PATHS.append("{root}/python/griptape-nodes-library-standard/griptape_nodes_library.json")
    env.GTN_LIBRARY_WORKER_REQUESTS.append(literal("Griptape Nodes Library=griptape_nodes_library_standard==0.87.0"))
```

`literal()` tells Rez to take the value as written, without expanding anything in it. So the
libraries an artist gets are exactly the libraries the resolve contains, at the versions Rez chose.
The engine never scans stores or picks versions.

## The launch package

`griptape-nodes-rez write-launch` writes `griptape_launch`, a package that exists to be required.

- Its `requires` list `griptape_nodes_engine==<version>` and `<family>_edit==<version>` for each
  library you chose.
- Its `commands()` set the variables below for the platform the environment resolves on
  (`system.platform`). They stop on a platform that was not configured.

| Variable | Value | From |
|:--|:--|:--|
| `GTN_CONFIG_LIBRARY__PROVISIONED_BY` | `environment` | always |
| `GTN_CONFIG_WORKER__COMMAND_PREFIX` | The prefix above, with this platform's Rez tools folder | `--tools` |
| `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED` | `true`, on every platform | `--allow-sandbox` |
| `XDG_CONFIG_HOME` and any other variable | As given, on every platform | `--set NAME=VALUE` |
| `REZ_CONFIG_FILE` | Your rezconfig appended after the studio's | `--config-file` (testing) |

- The prefix is written with `literal()`, so Rez leaves the placeholders for the engine.
- Without `--allow-sandbox`, `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED` is left out, so environment mode
  keeps the sandbox off.
- `XDG_CONFIG_HOME` is the folder holding the project's `griptape_nodes/griptape_nodes_config.json`.
- With `--config-file`, every worker's `rez env` reads your rezconfig.
- `--testing-store` sets nothing. It is only recorded in the package. See
  [Deployment](deployment.md#5-the-launch-package).

The prefix uses the full path to `rez` for each platform. In a resolved environment Rez's tools are
also on `PATH` by default (`rez_tools_visibility = "append"`,
[Configuring Rez](https://rez.readthedocs.io/en/stable/configuring_rez.html)). A full path keeps a
`PATH` change from picking another `rez`.

For other ways, such as a launcher profile, see
[Choosing environments](choosing-environments.md).

## Starting `gtn` with `--norc`

Start Griptape Nodes with `--norc` on macOS and Linux:

```bash
rez env --norc griptape_launch -- gtn
```

`rez env <request> -- <command>` runs the command through the artist's shell. On macOS and Linux
the shell's startup files (`~/.zshrc`, `~/.bashrc`) run after Rez sets `PATH`.

If one of them puts another `gtn` in front, that `gtn` runs instead, with its own engine and editor.
For example: `export PATH="$HOME/.local/bin:$PATH"` after a `uv tool install griptape-nodes`.
`--norc` skips the startup files ([rez-env](https://rez.readthedocs.io/en/stable/commands/rez-env.html)),
and the worker prefix uses it for the same reason.

Windows shells (cmd, PowerShell) ignore the flag and read no such files for a command.
