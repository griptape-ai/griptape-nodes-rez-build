# Libraries

A Griptape node library declares two sets of pip dependencies in its manifest
(`griptape_nodes_library.json`):

- `pip_dependencies`: what the editor needs to import the library's nodes and show them.
- `pip_dependencies_exec`: heavy packages only a running node needs, such as torch or diffusers.

`griptape-nodes-rez` keeps that split by building two Rez packages per library.

## Two packages per library

| Package | Contains | Requires | Used by |
|:--|:--|:--|:--|
| `<family>_edit` | The library's source and manifest, in `python/<repo or folder name>/` | The `pip_dependencies`, and the `_edit` packages of its library dependencies | The editor, through the launch package |
| `<family>` | Nothing of its own | `<family>_edit==<version>` and the `pip_dependencies_exec` | The library's worker |

`<family>` is the library's repo or folder name, in lowercase with underscores:
`griptape-nodes-library-diffusers` becomes `griptape_nodes_library_diffusers`, with packages
`griptape_nodes_library_diffusers_edit` and `griptape_nodes_library_diffusers`.

The source keeps its repo or folder name inside the package, and the `_edit` package publishes the
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

(The pip ranges are illustrative.)

- **Pip dependencies** use the version ranges from the manifest's specifiers, converted to Rez
  ranges the way `rez-pip` converts them (`>=1.26,<3` becomes `1.26+<3`). There are no pins from a
  lock file. A specifier with no Rez equivalent fails the build instead of meaning any version.
- **The version** comes from a `pyproject.toml` next to the manifest (or up to four folders above),
  then `git describe --tags`, then the manifest's `library_version`, then `1.0.0`.

- The editor's environment contains only `_edit` packages, so torch and other heavy dependencies
  never enter it. This matches the edit/execution split Griptape Nodes uses with virtual
  environments.
- A worker resolves `<family>==<version>`, which brings the `_edit` package (the library code) and the
  execution dependencies.
- A library with no execution dependencies still gets a `<family>` package that only requires its
  `_edit` package, so every library's worker request has the same form.
- The request the `_edit` package publishes pins the exact version, so the worker gets the library
  version the editor loaded, even if a newer one is released while the editor is open.

The build tool writes both packages from the one manifest; nobody maintains them by hand.

## Library dependencies

A library can declare that it needs another library (`library_dependency` in its manifest, a git
URL and ref). The build tool builds each declared library first, cloning it, unless the store
already has an `_edit` package for it (`--rebuild` rebuilds it anyway). It then adds the other
library's `_edit` package to this library's `_edit` `requires`, without a version: which version is
used is up to the request and Rez, like any other package.

- Rez puts the dependency in the environment, and the dependency's own `commands()` add it to
  `GTN_LIBRARY_PATHS`. The engine registers it like any other library. It never downloads one.
- A missing required dependency is a resolve error when the environment starts, reported by Rez,
  not a problem the engine has to find later.
- The dependency's packages must be in a store Rez searches when artists use it: release them too.
- The engine recognises the dependency by its repository name in the folder path of each loaded
  library's manifest, ignoring letter case and treating `-` and `_` alike. Packages from the build
  tool keep the source in `python/<repo name>/`, so this works without anything to configure. An
  environment built another way needs a folder named after the repository in the manifest's path
  (for example `.../griptape_nodes_library_openexr/griptape_nodes_library.json` for
  `griptape-nodes-library-openexr`), or the library reports the dependency as missing.

### Optional dependencies: weak references

A dependency declared with `required: false` becomes a **weak reference**, written with a leading
`~`:

```python
requires = ["~griptape_nodes_library_standard_edit"]
```

The Rez docs define a weak reference as one that forces "a package version to be within the
specified range if present, but does not actually require the package"
([Basic Concepts](https://rez.readthedocs.io/en/stable/basic_concepts.html)). The build tool
writes it without a range, so an optional library is used when the environment includes it and is
simply absent otherwise. The VFX Reference Platform package is built entirely from weak references
such as `~ocio-2.1`
([nerdvegas/rez-recipes-vfxrefplatform](https://github.com/nerdvegas/rez-recipes-vfxrefplatform)).

## Choosing which libraries an environment has

Libraries are chosen by the request that starts Griptape Nodes: the `--library` options of the
launch package, a launcher profile, or a project config. Changing libraries means starting a new
environment, as with any application started through Rez. See
[Choosing environments](choosing-environments.md).

Library entries in the project's `libraries_to_register` do not choose libraries here. With `library.dependency_source` set to `"environment"`, an entry that the environment
does not provide does not load, and the library list says so.

### Artists' own nodes: the sandbox

By default the Sandbox Library does not load either. A studio that wants artists to write their own
nodes can allow it, and only it, with `write-launch --allow-sandbox` (or the matching setting in a
launcher profile or project config). The sandbox then loads in the editor's process with no virtual
environment of its own, so it can import only what the launch environment holds: the engine and
the libraries' edit-time packages, not execution-only packages such as torch. **Refresh Sandbox**
reloads it without a relaunch and reopens the workflow so sandbox nodes use the new code. Every
other library still comes only from the environment. See [Allowing a sandbox library](configuration.md#allowing-a-sandbox-library).

## When libraries do not resolve together

The editor's environment resolves the engine and every chosen library's edit-time dependencies in
one go. If two libraries need versions that cannot coexist, `rez env griptape_launch` fails before
the editor opens, and Rez names the conflicting packages ("the resolve will fail, and rez will inform
you", [Basic Concepts](https://rez.readthedocs.io/en/stable/basic_concepts.html)).

- Only edit-time dependencies meet in the editor. Execution dependencies meet only inside each
  library's own worker, so two libraries needing different torch versions can share an environment.
- Resolve your library set together once (`rez env griptape_launch`) before handing it to
  artists.
- If a set does not resolve, give each show or task a launch package with only the libraries it
  needs.

## Troubleshooting

| Symptom | Likely cause |
|:--|:--|
| `rez env griptape_launch` fails naming two packages | Two libraries' edit-time dependencies conflict; see above. |
| A library in the project config shows "not provided by the environment" | It is not in the launch package (or profile) requests. Add its `_edit` package there. |
| A sandbox node fails with an import error | Sandbox nodes run in the editor's process with no virtual environment. The package must be an edit-time dependency of a library in the environment, or requested by the launch request itself; execution-only packages (`pip_dependencies_exec`) are not available there. |
| **Refresh Libraries** is greyed out | Expected in environment mode: relaunch to pick up library changes. With a sandbox allowed, the button reads **Refresh Sandbox**. |
| A worker's `rez env` fails naming `python-3.x` | Workers run the editor's Python, and the library's execution packages do not support it. Build the library with the Python the editor runs, or give the editor a Python the library supports. |
| `ImportError` in the editor, not in the worker | The module belongs in `pip_dependencies` (edit-time), not `pip_dependencies_exec`. |
| Running a node says the environment does not say which packages its worker needs | Its `_edit` package did not publish `GTN_LIBRARY_WORKER_REQUESTS`, or the entry's name differs from the manifest's `name`; rebuild it with `griptape-nodes-rez build-library`. |
| A library reports a missing library dependency that is in the environment | The dependency's manifest path has no folder named after its repository (environments not built by `griptape-nodes-rez`). |
