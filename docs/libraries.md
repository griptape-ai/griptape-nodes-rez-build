# Libraries

Each Griptape node library becomes two Rez packages: one for the editor and one for the library's
worker.

## Two packages per library

A library's manifest (`griptape_nodes_library.json`) declares two sets of pip dependencies:

- `pip_dependencies`: what the editor needs to import the library's nodes and show them.
- `pip_dependencies_exec`: heavy packages only a running node needs, such as torch or diffusers.

`griptape-nodes-rez` keeps that split. It writes both packages from the one manifest, so nobody
maintains them by hand.

| Package | Contains | Requires | Used by |
|:--|:--|:--|:--|
| `<family>_edit` | The library's source and manifest, in `python/<repo or folder name>/` | The `pip_dependencies`, and the `_edit` packages of its library dependencies | The editor, through the launch package |
| `<family>` | Nothing of its own | `<family>_edit==<version>` and the `pip_dependencies_exec` | The library's worker |

`<family>` is the library's repo or folder name, in lowercase with underscores.
`griptape-nodes-library-diffusers` becomes `griptape_nodes_library_diffusers`, with packages
`griptape_nodes_library_diffusers_edit` and `griptape_nodes_library_diffusers`.

The source keeps its repo or folder name inside the package. The `_edit` package publishes the
library to the engine in its `commands()`:

```python
# griptape_nodes_library_diffusers_edit/0.40.1/package.py (written by griptape-nodes-rez)
requires = ["pillow-10+", "griptape_nodes_library_standard_edit"]


def commands():
    env.PYTHONPATH.append("{root}/python/griptape-nodes-library-diffusers")
    env.GTN_LIBRARY_PATHS.append("{root}/python/griptape-nodes-library-diffusers/griptape_nodes_library.json")
    env.GTN_LIBRARY_WORKER_REQUESTS.append(
        literal("Griptape Modular Diffusion Nodes Library=griptape_nodes_library_diffusers==0.40.1")
    )


# griptape_nodes_library_diffusers/0.40.1/package.py
requires = ["griptape_nodes_library_diffusers_edit==0.40.1", "torch-2.7+", "diffusers-0.40+"]
```

The pip ranges are illustrative.

- The editor's environment contains only `_edit` packages, so torch and other heavy dependencies
  never enter it. This matches the edit/execution split Griptape Nodes uses with virtual
  environments.
- A worker resolves `<family>==<version>`, which brings the `_edit` package (the library code) and
  the execution dependencies.
- A library with no execution dependencies still gets a `<family>` package that only requires its
  `_edit` package. Every library's worker request has the same form.
- The `_edit` package's worker request pins the exact version. The worker gets the version the
  editor loaded, even if a newer one is released while the editor is open.

### Where versions come from

Pip dependencies use the manifest's specifier ranges, converted the way `rez-pip` converts them:
`>=1.26,<3` becomes `1.26+<3`. There are no pins from a lock file. A specifier with no Rez
equivalent fails the build instead of meaning any version.

The library's own version comes from the first of these found:

1. A `pyproject.toml` in the manifest's folder or up to three folders above it, never above the git
   repository root.
2. `git describe --tags`.
3. The manifest's `library_version`.
4. `1.0.0`.

## Library dependencies

A library can declare that it needs another library with `library_dependency` in its manifest: a
git URL and ref. The build tool clones and builds each declared library first, unless the store
already has an `_edit` package for it. `--rebuild` rebuilds it anyway.

It then adds the other library's `_edit` package to this library's `_edit` `requires`, without a
version. Which version is used is up to the request and Rez, like any other package.

- Rez puts the dependency in the environment, and its own `commands()` add it to
  `GTN_LIBRARY_PATHS`. The engine registers it like any other library. It never downloads one.
- A missing required dependency is a resolve error when the environment starts, reported by Rez,
  so the engine never has to find it later.
- The dependency's packages must be in a store Rez searches when artists use it. Release them too.

### How the engine recognises a dependency

The engine looks for the dependency's repository name in the folder path of each loaded library's
manifest. It ignores letter case and treats `-` and `_` alike. Packages from the build tool keep
the source in `python/<repo name>/`, so this needs no configuration.

An environment built another way needs a folder named after the repository in the manifest's path,
or the library reports the dependency as missing. For example, `griptape-nodes-library-openexr`
needs `.../griptape_nodes_library_openexr/griptape_nodes_library.json`.

### Optional dependencies: weak references

A dependency declared with `required: false` becomes a **weak reference**, written with a leading
`~`:

```python
requires = ["~griptape_nodes_library_standard_edit"]
```

The Rez docs define a weak reference as one that forces "a package version to be within the
specified range if present, but does not actually require the package"
([Basic Concepts](https://rez.readthedocs.io/en/stable/basic_concepts.html)). The build tool writes
it without a range, so an optional library is used when the environment includes it and is absent
otherwise.

The VFX Reference Platform package is built entirely from weak references such as `~ocio-2.1`
([nerdvegas/rez-recipes-vfxrefplatform](https://github.com/nerdvegas/rez-recipes-vfxrefplatform)).

## Choosing which libraries an environment has

The request that starts Griptape Nodes chooses the libraries: the `--library` options of the launch
package, a launcher profile, or a project config. Changing libraries means starting a new
environment, as with any application started through Rez. See
[Choosing environments](choosing-environments.md).

The project's `libraries_to_register` does not choose libraries here. With `library.provisioned_by`
set to `"environment"`, an entry the environment does not provide does not load, and the library
list says so.

### Artists' own nodes: the sandbox

By default the Sandbox Library does not load either. A studio can allow it, and only it, with
`write-launch --allow-sandbox` or the matching setting in a launcher profile or project config.

The sandbox loads in the editor's process with no virtual environment of its own. It can import
only what the launch environment holds: the engine, the libraries' edit-time packages, and packages
the launch request asks for. Execution-only packages (`pip_dependencies_exec`) such as torch are
not available.

**Refresh Sandbox** reloads it without a relaunch and reopens the workflow so sandbox nodes use the
new code. Every other library still comes only from the environment. See
[Allowing a sandbox library](configuration.md#allowing-a-sandbox-library).

## When libraries do not resolve together

The editor's environment resolves the engine and every chosen library's edit-time dependencies in
one go. If two libraries need versions that cannot coexist, `rez env griptape_launch` fails before
the editor opens. Rez names the conflicting packages ("the resolve will fail, and rez will
inform you", [Basic Concepts](https://rez.readthedocs.io/en/stable/basic_concepts.html)).

- Execution dependencies meet only inside each library's own worker, so two libraries needing
  different torch versions can share an environment.
- Resolve your library set together once (`rez env griptape_launch`) before handing it to
  artists.
- If a set does not resolve, give each show or task a launch package with only the libraries it
  needs.

## Troubleshooting

| Symptom | Cause | Fix |
|:--|:--|:--|
| `rez env griptape_launch` fails naming two packages | Edit-time dependencies conflict | See [above](#when-libraries-do-not-resolve-together) |
| A project library shows "not provided by the environment" | Not in the launch package (or profile) requests | Add its `_edit` package there |
| A sandbox node fails with an import error | The package is not in the launch environment | See [the sandbox](#artists-own-nodes-the-sandbox) |
| **Refresh Libraries** is greyed out | Expected in environment mode | Relaunch. With a sandbox allowed, it reads **Refresh Sandbox** |
| A worker's `rez env` fails naming `python-3.x` | Workers run the editor's Python, which the library's execution packages do not support | See [Python mismatches](#python-mismatches) |
| A build stops with "its wheel is built for cp3XX-..., which Python 3.YY cannot import" | The package index has no wheel of that version for the tool's Python | See [Python mismatches](#python-mismatches) |
| A compiled package fails to import (`No module named '..._multiarray_umath'`, `.so` errors) | Installed for another Python, in a store built before this check existed | Rebuild with `--rebuild` using the current build tool |
| `ImportError` in the editor, not in the worker | The module is in `pip_dependencies_exec` | Move it to `pip_dependencies` (edit-time) |
| Running a node says the environment does not say which packages its worker needs | See [below](#missing-worker-request) | Rebuild with `griptape-nodes-rez build-library` |
| A library reports a missing library dependency that is in the environment | No folder named after its repository in the manifest path | See [How the engine recognises a dependency](#how-the-engine-recognises-a-dependency) |

### Python mismatches

For a worker failing on `python-3.x`, build the library with the Python the editor runs, or give the
editor a Python the library supports.

For a missing wheel, run the tool with a Python the package supports, or pin a version of the
package that has a wheel for it.

### Missing worker request

The library's `_edit` package did not publish `GTN_LIBRARY_WORKER_REQUESTS`, or the entry's name
differs from the manifest's `name`.
