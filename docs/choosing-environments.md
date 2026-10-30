# Choosing environments

The Rez request that starts Griptape Nodes decides which engine and which libraries an artist gets.
Any way of building that request works, because `GTN_LIBRARY_PATHS` is filled by whatever `_edit`
packages the request contains.

This page describes three, and what each carries besides the package list.

## What every environment needs

1. The packages: `griptape_nodes_engine` and the `<family>_edit` package of each library.
2. The engine settings from [Configuration](configuration.md): `library.provisioned_by` set to
   `"environment"`, and `worker.command_prefix` for libraries that run workers.
   `GTN_LIBRARY_PATHS` and `GTN_LIBRARY_WORKER_REQUESTS` come from the `_edit` packages themselves.
   Optionally, set `library.sandbox_enabled` to `true` to let artists keep a sandbox library.
   Environment mode refuses it otherwise.
3. The project's Griptape Nodes configuration, usually by setting `XDG_CONFIG_HOME` to the folder
   holding `griptape_nodes/griptape_nodes_config.json`, with `websocket_direct` enabled for the
   bundled editor.
4. `--norc` on macOS and Linux, or artists' shell startup files kept free of other `gtn` installs.
   See [Configuration](configuration.md#starting-gtn-with---norc).
5. For torch libraries, a torch build per machine. Add the `implicit_packages` line from
   `griptape-nodes-rez gpu-config` to each machine's rezconfig. This does not depend on how the
   environment is started. The torch packages themselves stop an environment whose build Rez picked
   on its own (see [GPU builds per machine](gpu-builds.md#the-guard-no-build-no-guess)).

## Option 1: a launch package

A launch package is a package that exists to be required. It lists the engine and libraries in its
`requires` and sets the rest in its `commands()`. `griptape-nodes-rez write-launch` writes one:

```bash
griptape-nodes-rez write-launch --tools osx=/Volumes/pipeline/rez/bin/rez ... \
    --library griptape_nodes_library_standard --library griptape_nodes_library_diffusers-0.40.1 \
    --set XDG_CONFIG_HOME=/studio/projects/my_show/config \
    --local-packages-path /studio/griptape/rez/packages/local

rez env --norc griptape_launch -- gtn
```

- It covers needs 1 and 2 in one package, per platform. It covers need 3 when you pass
  `--set XDG_CONFIG_HOME=...`, as above. Need 4 is the `--norc` in the `rez env` command artists run.
- It is released and versioned like any package: a new library set is a new `griptape_launch`
  version.
- For several shows or tasks, write one launch package per set, with a different `--version` or
  family name each. This also keeps library sets small enough to resolve together (see
  [Libraries](libraries.md#when-libraries-do-not-resolve-together)).

This is the simplest place to start, and the one the build tool writes for you.

## Option 2: a launcher profile

Some studios start tools from a launcher whose profiles list Rez requests, such as
[kloch](https://github.com/knotsanimation/kloch), [toolbox](https://github.com/dylan-neill/toolbox),
and [rez-studio-launcher](https://github.com/Ubik42/rez-studio-launcher).

The profile can request a launch package (`griptape_launch-1.2`), and nothing else changes.

Or the profile can list the packages itself:

```yaml
# illustrative, not any launcher's exact format
requires:
  - griptape_nodes_engine-0.103
  - griptape_nodes_library_standard_edit-0.87
  - griptape_nodes_library_diffusers_edit-0.40
```

Then the engine settings must come from elsewhere. One option is environment variables set by the
launcher, if it can set them:

- `GTN_CONFIG_LIBRARY__PROVISIONED_BY=environment`
- `GTN_CONFIG_WORKER__COMMAND_PREFIX` as a JSON list
- `GTN_CONFIG_LIBRARY__SANDBOX_ENABLED=true`, if artists may keep a sandbox library (see
  [Allowing a sandbox library](configuration.md#allowing-a-sandbox-library))

The other is the project's `griptape_nodes_config.json`:

```json
{
  "library": { "provisioned_by": "environment", "sandbox_enabled": false },
  "worker": {
    "command_prefix": ["rez", "env", "--norc", "griptape_nodes_engine=={engine_version}", "python-{python_version}", "{library_request}", "--"]
  }
}
```

A config file shared across platforms cannot hold each platform's path to Rez, so this uses plain
`rez`. Inside a resolved environment Rez's tools are on `PATH` by default
(`rez_tools_visibility = "append"`,
[Configuring Rez](https://rez.readthedocs.io/en/stable/configuring_rez.html)).

Whether `--norc` is passed depends on the launcher.

## Option 3: a project config

Some pipelines keep a project-level config that fixes the version of each tool. List the engine and
`_edit` packages with their versions there. Provide the engine settings with environment variables
or the project's `griptape_nodes_config.json`, as in option 2.

If Griptape Nodes runs on a farm, record the request and the submit time with each job. Then resolve
on the farm with `rez env --time <submit time> --no-local`. A farm job gets the environment the
artist had, and never a package from someone's local store
([rez-env](https://rez.readthedocs.io/en/stable/commands/rez-env.html)).

## Which to choose

| | Launch package | Launcher profile | Project config |
|:--|:--|:--|:--|
| Written by | `griptape-nodes-rez write-launch` | Your launcher's tools | Your pipeline |
| Engine settings | In the package | Launcher environment or project `griptape_nodes_config.json` | Same as launcher profile |
| Per-platform Rez path in the prefix | Yes | Plain `rez` from `PATH` | Plain `rez` from `PATH` |
| Changing libraries | New package version | Edit the profile | Edit the project config |

They combine. A launcher profile or project config can request a launch package instead of listing
libraries itself.
