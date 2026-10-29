# Releasing

`griptape-nodes-rez` never releases packages. It only writes to the local store you pass with
`--local-packages-path`. Promoting a tested build to production is a Rez administrator's decision,
made with Rez's own tools. This page gives the steps and an example script to adapt.

## Before you release

Release once every platform you support has built into the local store (see
[One store, several platforms](deployment.md#4-one-store-several-platforms)). Every torch build
you want must be in it too (see [GPU builds per machine](gpu-builds.md)).

## Promoting with `rez-cp`

A release copies a family **and every package its resolve pulls from the local store** into your
release location. `rez-cp` copies a package with its payload and every variant, including the
per-torch-build variants (folders starting with a dot, such as `.torch_backend-cu128`).

1. Resolve the family with the local store first, then your production paths. The resolve lists
   every package the family needs.
2. Copy each resolved package that came from the local store and is not already released. Leave
   packages from studio stores alone (`python`, `platform`, `os`, `arch`, DCC tools).
3. Validate that the family resolves from the production paths alone, without the local store.

Good to know:

- Rez family names never contain `-`, but versions can (`os-osx-26.5.1`). Split `family-version`
  at the **first** hyphen.
- Never overwrite a released version. Build a new version instead. Avoid `rez-cp -o` for
  production.
- `--allow-empty` is needed for the first copy into an empty release location.
- Don't rely on `rez-cp --dry-run`. In Rez 3.4.0 it fails on packages without variants, such as the
  engine and library packages. The example scripts' dry run lists packages instead.
- One resolve covers one platform and one torch build. Run step 1 on each platform, or with each
  `.torch_backend-<build>` request, so every package a platform or build needs is copied.

## What to release, in order

1. Each library, by its full package `<family>`. Its resolve includes `<family>_edit` and every
   dependency, so one run copies both. Run once per platform, and once per torch build with
   `EXTRA_REQUESTS=".torch_backend-<build>"`.
2. `griptape_nodes_engine`.
3. The launch package, last, once everything it requires is released.

The engine package declares version ranges, so the resolve on release day decides which dependency
versions get copied. To reproduce an environment later, resolve at a fixed time with
`rez env --time` ([rez-env](https://rez.readthedocs.io/en/stable/commands/rez-env.html)), or
release a launch package that pins the versions you tested.

## Example script

[`examples/release_family.sh`](../examples/release_family.sh) implements the three steps, and
[`examples/release_family.ps1`](../examples/release_family.ps1) does the same in PowerShell. Logging,
approvals, permissions, and notifications are yours to add.

```bash
examples/release_family.sh FAMILY LOCAL_STORE RELEASE_STORE "PRODUCTION_PATHS" [--dry-run]

# Promote the diffusers library and its dependencies:
examples/release_family.sh griptape_nodes_library_diffusers \
    /studio/griptape/rez/packages/local \
    /studio/rez/packages/release \
    "/studio/rez/packages/release:/studio/rez/packages"

# ...and its cu118 torch build as well:
EXTRA_REQUESTS=".torch_backend-cu118" examples/release_family.sh griptape_nodes_library_diffusers ...
```

`PRODUCTION_PATHS` is the search path artists use, **without** the local store. Running the script
again is safe: already-released versions are skipped.

On Windows, use the PowerShell script, and separate `PRODUCTION_PATHS` with `;`:

```powershell
.\examples\release_family.ps1 griptape_nodes_library_diffusers `
    P:\griptape\rez\packages\local `
    P:\rez\packages\release `
    "P:\rez\packages\release;P:\rez\packages"

# ...and its cu118 torch build as well:
$env:EXTRA_REQUESTS = ".torch_backend-cu118"
.\examples\release_family.ps1 griptape_nodes_library_diffusers ...
```

Add `-DryRun` (or `--dry-run` for the bash script) to list what would be copied without copying.
