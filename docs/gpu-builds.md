# GPU builds per machine

A torch build only runs on NVIDIA drivers new enough for its CUDA version, and the newest cards need
the newest builds: an RTX 50-series card needs a CUDA 12.8 build (`cu128`), while an older
workstation on an older driver needs `cu118`. Machines without an NVIDIA GPU are best served by the
CPU build.

**The engine does not choose a build.** Each machine's Rez
configuration says which build it uses, and Rez picks it when a worker's environment resolves. The
build tool prints that configuration for you, and torch packages refuse to run a build Rez picked
on its own.

## Torch builds as variants

On Linux and Windows, one store holds several torch builds as
[variants](https://rez.readthedocs.io/en/stable/variants.html) of the same torch package, each tagged
with a Rez ephemeral named after uv's torch backends:

```python
# torch/2.7.0/package.py
variants = [
    ["platform-osx", "arch-arm64", "python-3.12"],  # macOS: one build
    ["platform-windows", "arch-AMD64", "python-3.12", ".torch_backend-cu118"],
    ["platform-windows", "arch-AMD64", "python-3.12", ".torch_backend-cu128"],
]
```

- Library packages keep requiring plain `torch`, with the manifest's range. The build is chosen when
  the environment resolves.
- Ephemerals are not packages, so nothing extra is written or released, and nothing can collide with
  a `cuda` package your studio already has
  ([Ephemerals](https://rez.readthedocs.io/en/stable/ephemerals.html)).
- macOS has a single torch build, so its variant carries no tag and needs no configuration.
- The same applies to every package built per torch build (torch, torchvision, ...).
- Only the `<family>` package (the worker's) pulls in torch. The editor's environment never contains
  it.

## Building the builds you need

```bash
griptape-nodes-rez build-library \
    --git-url https://github.com/griptape-ai/griptape-nodes-library-diffusers.git \
    --torch-backend cu128 --torch-backend cpu \
    --local-packages-path /studio/griptape/rez/packages/local
```

- Any machine can build every build for its operating system: the build downloads files and does
  not need the GPU.
- `--torch-backend all` builds every torch build.
- The flag replaces the library's own torch source, such as a pinned PyTorch index in its manifest,
  with each chosen build. It is ignored on macOS.
- `--torch-backend=auto` in a manifest's `pip_install_flags`, and `UV_TORCH_BACKEND` in the shell,
  are refused: they make uv pick torch for the GPU of the machine running the build, which would put
  that machine's build into a store every workstation shares.

Each Windows CUDA build of torch is about 2.5 GB, so build the ones your workstations need.

## Telling each machine its build

The Rez docs describe this exact case on their *Ephemerals* page: a package with variants requiring
`.gpu-0` (CPU) or `.gpu-1` (GPU), and a setup that "includes a `.gpu-1` ephemeral in the implicits
list on all GPU-enabled hosts"
([Ephemerals](https://rez.readthedocs.io/en/stable/ephemerals.html)). Implicit packages are
"packages that are implicitly added to all package resolves"
([Configuring Rez](https://rez.readthedocs.io/en/stable/configuring_rez.html)).

For Griptape Nodes the ephemeral is `.torch_backend-<build>`. Add it to the machine's implicit
packages in a rezconfig layered after your studio's:

```python
# this workstation's rezconfig, added to REZ_CONFIG_FILE
implicit_packages = ModifyList(append=[".torch_backend-cu128"])
```

Every resolve on that machine then picks the `cu128` variant, including plain `rez env` outside
Griptape Nodes. Public setups compute implicit packages per machine: LucaScheller's rezconfig
works out its implicits when it loads (`~platform=={system.platform}`, plus a container bootstrap
package when one is present) ([VFX-RezRecipes](https://github.com/LucaScheller/VFX-RezRecipes)).

### `gpu-config`: the snippet for this machine

Run on the workstation:

```bash
griptape-nodes-rez gpu-config --local-packages-path /studio/griptape/rez/packages/local
```

```python
# Torch build for render-042 (linux-x86_64): NVIDIA driver supports CUDA 12.8; builds in the store: cpu, cu118, cu128.
# Add to this machine's rezconfig (never to one shared by machines with different GPUs):
implicit_packages = ModifyList(append=[".torch_backend-cu128"])
```

It reads the highest CUDA version the NVIDIA driver supports (from `nvidia-smi`) and picks the
newest CUDA build in the store that driver can run, or `cpu` on a machine without an NVIDIA GPU. It
looks at the builds in the stores you give with `--local-packages-path` (repeatable), or on Rez's
search path without it. On macOS there is nothing to configure.

It only prints. Where the line goes (a per-machine file, your deployment tooling, a configuration
computed at load) is your decision, and nothing in Griptape Nodes writes a rezconfig.

## The guard: no build, no guess

The *Ephemerals* page warns that a host without the implicit may get either variant: Rez satisfies an
ephemeral in a variant just by resolving it. For CUDA that means a wrong build that fails when a node
runs, or a CUDA build on a machine with no NVIDIA GPU.

So every package built per torch build (torch, torchvision, ...) checks, in its `commands()`, how its
build was chosen:

```python
builds = [str(r) for r in this.variant_requires if str(r).startswith('.torch_backend-')]
if builds and '.torch_backend' not in implicits and '.torch_backend' not in request:
    stop(... 'run "griptape-nodes-rez gpu-config" and add the implicit_packages line it prints ...')
```

The build is read from the resolved variant's own requirements rather than Rez's `ephemerals`
object, which Rez 3.4.0 can leave empty when another package in the resolve has a late-bound
`requires()`. Packages built by older versions of the tool carry a guard that does not fire; rebuild
them with `--rebuild`.

- A build that Rez picked on its own (not named by the machine's implicit packages, and not requested
  explicitly) stops the environment with that message. The worker's `rez env` fails, so the library's
  worker does not start instead of running the wrong build. A plain `rez env` of the library on that
  machine shows the same message.
- The fix is on the machine: run `griptape-nodes-rez gpu-config` there and add the
  `implicit_packages = ModifyList(append=[".torch_backend-cuXXX"])` line it prints to that machine's
  rezconfig.
- An explicit `.torch_backend-<build>` in the request also satisfies the check, for a one-off test.
- Packages without torch builds, libraries that do not use torch, and torch on macOS are unaffected.
- The check is in the torch packages themselves, so it applies however the environment is started:
  launch package, launcher profile, or project config.

The alternative is to set the implicit on every machine up front: one rezconfig per GPU class
(`.torch_backend-cu128` on RTX hosts, `.torch_backend-cpu` on CPU-only farm nodes), deployed the way
your studio deploys other per-host configuration. The Rez docs suggest the same: set the implicit on
every host, or request the build explicitly.

## Releasing torch builds

One resolve covers one torch build. Release each build you want with
`EXTRA_REQUESTS=".torch_backend-<build>"` (see [Releasing](releasing.md)).

## Limits

- `gpu-config` reads the driver, not the card's architecture. If a newer CUDA build drops support for
  a very old card, set that machine's implicit by hand.
- Packages compiled against torch (for example xformers or flash-attn) are not built per torch build
  yet.
- Linux's default torch from PyPI carries no build tag. Build Linux libraries with `--torch-backend`
  to get tagged builds.
