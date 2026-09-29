# Configuration

The engine reads two settings and two environment variables, plus one optional setting for
studios that let artists keep a sandbox library. None of
them mention Rez: they say "the environment provides the libraries" and "start workers with this
command in front". A Rez launch package sets all four, but anything that can set environment
variables can provide them. The engine's own guide, *Running in a Managed Environment*
(`docs/guides/managed_environment.md` in the engine repository), describes them for any tool; this
page describes them as a Rez setup uses them.

## Engine settings

| Setting | Values | Environment override |
|:--|:--|:--|
| `library.dependency_source` | `"venv"` (default) or `"environment"` | `GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE` |
| `worker.command_prefix` | A list of strings, default `[]` | `GTN_CONFIG_WORKER__COMMAND_PREFIX`, as a JSON list |
| `library.environment_allows_sandbox` | `false` (default) or `true` | `GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX` |

Like other engine settings, they can live in `griptape_nodes_config.json` or come from the
`GTN_CONFIG_*` overrides. A launch package uses the overrides.

### `library.dependency_source`

With `"venv"`, the engine behaves as it always has: it builds a virtual environment for each
library and downloads libraries it is asked for.

With `"environment"`:

- Only the libraries in `GTN_LIBRARY_PATHS` load. Every other configured library (entries in
  `libraries_to_register`, and the Sandbox Library) is listed with the problem "This library was not
  loaded because the engine is running in an environment that provides its libraries, and this
  library is not one of them."
- Nothing is downloaded, updated, or synced; `libraries_to_download` is ignored. Downloading,
  updating, switching, or syncing a library from the editor fails with a message saying the
  environment manages libraries.
- Checking a library for updates reports no update and says updates come from the environment,
  without contacting its git remote.
- The Sandbox Library is neither scanned nor loaded, and adding a sandbox node from a file fails with
  the same message, unless the studio allows a sandbox (see
  [Allowing a sandbox library](#allowing-a-sandbox-library)).
- No `.venv` or `.venv-exec` folder is created, and none left from an earlier run is used.
- A library's `library_dependency` is satisfied only by a library in `GTN_LIBRARY_PATHS`, never by a
  download (see [Libraries](libraries.md#library-dependencies) for how the engine matches them).
- The artist's own config file is left alone: entries for libraries that did not load are not
  removed.
- The editor shows library settings as read-only and hides the Sandbox Settings pane (unless a
  sandbox is allowed), and each library shows where it came from.
- **Refresh Libraries** is greyed out everywhere it appears in the editor, with a tooltip saying
  libraries come from the environment the engine was started in and a relaunch picks up library
  changes. With a sandbox allowed, the button reads **Refresh Sandbox** instead (see below).

A misspelled value is reported in the engine log and ignored, rather than quietly falling back to
`"venv"`. A dependency the environment is missing shows up as an ordinary import error for that
library.

### Allowing a sandbox library

Environment mode is strict by default: only the libraries the environment publishes load. A studio
that wants artists to develop their own nodes can let one more library in, the Sandbox Library (the
folder in **Settings → Library → Sandbox Settings**), with `library.environment_allows_sandbox`:

- With `write-launch --allow-sandbox`, the launch package sets
  `GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX=true` on every platform.
- Without a launch package, set the same variable in the launcher profile, or
  `"library": {"environment_allows_sandbox": true}` in the project's `griptape_nodes_config.json`
  (see [Choosing environments](choosing-environments.md)).

Then:

- The Sandbox Library is scanned and loaded **in the editor's own process**, and artists can add
  sandbox nodes.
- No virtual environment is built for it. Sandbox nodes run in the editor's process, whose
  environment is the launch environment: the engine plus each library's **edit-time** packages
  (its `pip_dependencies`, brought in by `<family>_edit`). A library's execution-only packages
  (`pip_dependencies_exec`, such as torch) exist only in that library's worker, so sandbox nodes
  cannot import them. A node that imports something missing reports the import error.
- To make a package available to sandbox nodes, put it in the launch environment in one of two ways:
  as an edit-time dependency (`pip_dependencies`) of a library the environment includes, or as a
  package the launch request itself includes (a launcher profile or project config can request any
  package).
- Only the sandbox is let in. Every other library the environment does not list is still refused,
  and nothing is downloaded or installed.
- In the editor the button reads **Refresh Sandbox**. It reloads only the sandbox, so new or changed
  node files show up without relaunching; the environment's libraries stay loaded and their workers
  keep running. Like Refresh Libraries, it asks to save a workflow with unsaved changes, then reopens
  the workflow, so its sandbox nodes use the new code. Changes to the environment's own libraries
  still need a relaunch.

The setting has no effect when `library.dependency_source` is `"venv"`, where the sandbox always
works. It accepts `true` or `false` in any letter case; any other value from the environment
variable is reported in the engine log and ignored, which keeps the sandbox refused.

### `worker.command_prefix`

A library whose nodes need their own dependencies runs them in a separate worker process, started
with the engine's Python interpreter:
`<absolute path to python> -m griptape_nodes_app engine --library-name "..." ...`. The prefix is
placed in front of that command, so the worker starts inside whatever environment the prefix
creates. Placeholders are filled for each worker:

| Placeholder | Filled with |
|:--|:--|
| `{library_request}` | The library's entry in `GTN_LIBRARY_WORKER_REQUESTS` |
| `{library_name}` | The library's name, as in its manifest |
| `{engine_version}` | The running engine's version, such as `0.103.0` |

A word that is exactly `{library_request}` becomes one word per space-separated part of the entry,
so one entry can name several packages. Inside a longer word it is replaced as text.

The launch package sets, per platform:

```json
["/studio/rez/bin/rez/rez", "env", "--norc", "griptape_nodes_engine=={engine_version}", "{library_request}", "--"]
```

so a worker for `lib_foo` 1.4.2 starts as:

```bash
/studio/rez/bin/rez/rez env --norc griptape_nodes_engine==0.103.0 lib_foo==1.4.2 -- /path/to/python -m griptape_nodes_app engine ...
```

The interpreter path is the one the editor's environment runs, which is the `python` package that
environment resolved.

- **Exact versions.** The worker resolves the same engine and library versions the editor loaded. A
  newer library in the store does not change a running environment's workers.
- **A library with no request.** With `"environment"`, a library that has no entry in
  `GTN_LIBRARY_WORKER_REQUESTS` does not start a worker. Its nodes stay editable, and running one
  says the environment does not say which packages its worker needs. It never runs without the
  prefix. With `"venv"`, it runs without a prefix, as if none were configured.
- **The worker's environment.** The worker receives the engine's environment as it was when the
  engine started, plus a few variables the engine sets by name (`GTN_ENGINE_ID`,
  `GTN_ORCHESTRATOR_ENGINE_ID`, `PYTHONUNBUFFERED`, and the static file server address). The engine
  changes nothing else, so what the worker's environment finally contains is decided by `rez env`.
- **Torch builds** are picked by Rez inside the worker's resolve, from the machine's implicit
  packages. See [GPU builds per machine](gpu-builds.md).
- **Windows quoting.** Keep the prefix free of quoted arguments: some Windows shells drop double
  quotes inside arguments.

## Environment variables the engine reads

Both are lists separated by the platform's path separator (`:` on macOS and Linux, `;` on Windows).
Rez's `env.<VAR>.append(...)` joins values with that separator by default
([Package Commands](https://rez.readthedocs.io/en/stable/package_commands.html)).

| Variable | Entries | Set by |
|:--|:--|:--|
| `GTN_LIBRARY_PATHS` | Library manifests (`griptape_nodes_library.json`), or folders to search for them | Each `<family>_edit` package |
| `GTN_LIBRARY_WORKER_REQUESTS` | `<library name>=<request>`, where `<library name>` is the manifest's `name` | Each `<family>_edit` package |

`GTN_LIBRARY_PATHS` works with either dependency source: its libraries load before the
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

`griptape-nodes-rez write-launch` writes `griptape_launch`, a package that exists to be required. Its
`requires` list `griptape_nodes_engine==<version>` and `<family>_edit==<version>` for each library
you chose. Its `commands()` set, for the platform the environment resolves on (`system.platform`),
and stop on a platform that was not configured:

| Variable | Value | From |
|:--|:--|:--|
| `GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE` | `environment` | always |
| `GTN_CONFIG_WORKER__COMMAND_PREFIX` | The prefix above, with this platform's Rez tools folder, written with `literal()` so Rez leaves the placeholders for the engine | `--tools` |
| `GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX` | `true`, on every platform; left out otherwise, so the sandbox is refused | `--allow-sandbox` |
| `XDG_CONFIG_HOME` and any other variable | As given, on every platform. `XDG_CONFIG_HOME` is the folder holding the project's `griptape_nodes/griptape_nodes_config.json` | `--set NAME=VALUE` |
| `REZ_CONFIG_FILE` | Your rezconfig appended after the studio's, so every worker's `rez env` reads it | `--config-file` (testing) |

`--testing-store` sets nothing; it is only recorded in the package. See
[Deployment](deployment.md#5-the-launch-package).

The prefix uses the full path to `rez` for each platform. In a resolved environment Rez's tools are
also on `PATH` by default (`rez_tools_visibility = "append"`,
[Configuring Rez](https://rez.readthedocs.io/en/stable/configuring_rez.html)), but a full path keeps
a `PATH` change from picking another `rez`.

Other ways to provide the same settings, such as a launcher profile, are covered in
[Choosing environments](choosing-environments.md).

## Starting `gtn` with `--norc`

`rez env <request> -- <command>` runs the command through the artist's shell. On macOS and Linux the
shell's startup files (`~/.zshrc`, `~/.bashrc`) run after Rez sets `PATH`. If one of them puts
another `gtn` in front, for example `export PATH="$HOME/.local/bin:$PATH"` after a
`uv tool install griptape-nodes`, that `gtn` runs instead of the one in the environment, with its
own engine and editor. `--norc` skips the startup files
([rez-env](https://rez.readthedocs.io/en/stable/commands/rez-env.html)):

```bash
rez env --norc griptape_launch -- gtn
```

The worker prefix uses `--norc` for the same reason. Windows shells (cmd, PowerShell) ignore the flag
and read no such files for a command.

## Summary

| Name | Kind | Needed | Effect |
|:--|:--|:--|:--|
| `library.dependency_source` | engine setting | yes, `"environment"` | No venvs, downloads, or updates; only environment libraries load |
| `worker.command_prefix` | engine setting | for libraries with workers | Command placed in front of each worker's interpreter |
| `library.environment_allows_sandbox` | engine setting | optional | Lets the Sandbox Library load in environment mode; nothing else |
| `GTN_LIBRARY_PATHS` | environment variable | yes | Which libraries load (first, in either mode) |
| `GTN_LIBRARY_WORKER_REQUESTS` | environment variable | for libraries with workers | Each library's worker request |
| `REZ_CONFIG_FILE` (your studio's) | environment variable | as today | Used as-is; a launch package's `--config-file` only appends to it |
