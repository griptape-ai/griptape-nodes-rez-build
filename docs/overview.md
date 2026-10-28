# Running Griptape Nodes from Rez

`griptape-nodes-rez-build` builds Griptape Nodes as [Rez](https://rez.readthedocs.io) packages, so
your studio can start it like its other tools:

```bash
rez env --norc griptape_launch -- gtn
```

Rez builds the whole environment before `gtn` starts. The engine knows nothing about Rez. It reads
settings and environment variables the packages set, and loads the libraries they provide.

## How it fits together

```
Artist:        rez env --norc griptape_launch -- gtn
                 griptape_launch requires:
                   griptape_nodes_engine-0.103      (brings the gtn app and the editor)
                   lib_foo_edit-1.4  lib_bar_edit-2 (edit-time library packages)
                 its commands() set the engine settings below

Editor:        library.provisioned_by = "environment"
               libraries = GTN_LIBRARY_PATHS (published by the *_edit packages)
               no virtual environments, no downloads, no Rez calls
               (other configured libraries are listed as not provided by the environment)

Worker:        worker.command_prefix, filled per library from GTN_LIBRARY_WORKER_REQUESTS:
               rez env --norc griptape_nodes_engine==0.103.0 python-3.12 lib_foo==1.4.2 -- <python> -m griptape_nodes_app ...

Admin:         griptape-nodes-rez build-engine | build-library | write-launch | gpu-config
```

### The editor resolves once

`rez env griptape_launch` resolves the engine and the edit-time dependencies of every library in the
launch package together. If two libraries need versions that cannot coexist, the launch fails
before the editor opens, and Rez names the packages involved.

### Each worker resolves on its own

Heavy execution dependencies such as torch stay out of the editor. Each library's worker starts
inside its own `rez env`, with the exact engine and library versions the editor loaded.

The torch build comes from the machine's Rez configuration. A machine without one gets a clear stop
instead of a guessed build.

### Rez decides versions

Packages declare version ranges and the request picks what runs. Your rezconfig (package orderers,
implicit packages, `rez env --time`) applies to Griptape Nodes like any other tool. The engine never
scans stores or picks versions.

### Nothing in the engine is Rez-specific

The engine reads two settings and two environment variables. Anything that can set them can provide
libraries this way: Rez, conda activation scripts, or a container image. The engine's *Running in a
Managed Environment* guide (`docs/guides/managed_environment.md` in the engine repository)
describes them for any tool.

## What artists see

- The editor lists the libraries the environment provides, with their versions. Library settings
  are read-only.
- **Refresh Libraries** is greyed out, with a tooltip saying a relaunch picks up library changes.
  If your studio allows a sandbox library, the button reads **Refresh Sandbox** and reloads only the
  sandbox. See [Allowing a sandbox library](configuration.md#allowing-a-sandbox-library).
- Downloading or updating a library from the editor is refused. The environment manages libraries.
- A library listed in the project's config but not provided by the environment does not load. The
  library list says so.

## When something goes wrong

| Situation | What happens |
|:--|:--|
| Two libraries need conflicting edit-time versions | `rez env griptape_launch` fails before the editor opens, naming the packages |
| A required library dependency is missing | The launch resolve fails and Rez names the missing package |
| This machine has no torch build configured | The torch package stops the worker's `rez env`, saying to run `gpu-config` |
| A newer library version is released while artists work | Nothing changes until a new environment is started |
| The project lists a library the environment does not provide | Not loaded: listed as not provided by the environment |
| An artist adds a sandbox node | Refused, unless the studio allowed a sandbox |
| Another `gtn` first on `PATH` from shell startup files | That `gtn` runs instead. Start with `--norc` |

Without a torch build, the library's worker does not start. With a sandbox allowed, sandbox nodes
load, and a missing import is reported for that node.

## The pages

- [Deployment](deployment.md): building the engine and libraries with `griptape-nodes-rez`, stores,
  and building from several platforms.
- [Configuration](configuration.md): `library.provisioned_by`, `worker.command_prefix`,
  `GTN_LIBRARY_PATHS`, `GTN_LIBRARY_WORKER_REQUESTS`, the launch package, and `--norc`.
- [Libraries](libraries.md): the `<family>_edit` and `<family>` packages, library dependencies,
  and weak references.
- [GPU builds per machine](gpu-builds.md): torch builds as variants, `gpu-config`, implicit packages,
  and the guard for machines without one.
- [Choosing environments](choosing-environments.md): the launch package, launcher profiles, and
  project configs.
- [Releasing](releasing.md): promoting tested packages with `rez-cp`, and an example script.
