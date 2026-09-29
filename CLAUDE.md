# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`griptape-nodes-rez-build` builds Griptape Nodes as [rez](https://rez.readthedocs.io) packages for
studios: the engine (with the `gtn` app and the prebuilt editor), node libraries (an `_edit`
package and a full package each), the `griptape_launch` package artists start Griptape Nodes
with, and a per-machine rezconfig snippet for torch builds. The engine itself contains no rez
code; it reads `GTN_LIBRARY_PATHS`, `GTN_LIBRARY_WORKER_REQUESTS`,
`library.dependency_source`, and `worker.command_prefix`, which these packages set.

Those names are the contract with the engine. A rename has to land in the engine first.

For the full model (engine hooks, packages, torch guard, troubleshooting), read the skill in
`.agents/skills/griptape-nodes-rez-build/SKILL.md`.

## Development

```bash
make install    # uv sync with dev and test groups
make check      # ruff format check, ruff lint, pyright, typos
make fix        # auto-fix format and lint issues
make test/unit  # unit tests (parallel)
uv run griptape-nodes-rez --help
```

Iteration loop: make the change, run `make check` (or `make fix`), fix what it reports, run
`make test/unit`, continue.

## Rules specific to this repository

- **Never import `griptape_nodes`.** The engine checkout and library manifests are read as files
  (`pyproject.toml`, `griptape_nodes_library.json`). The tool must work with any engine version.
- **Never change rez configuration.** Rez runs with the environment the tool was started in;
  checks point rez at the store being built with `--paths`. Tests use temporary stores and a
  temporary `REZ_CONFIG_FILE`.
- **Every value in a generated `package.py` goes through `repr()`** (or `package_py` helpers).
  Values rez must not expand (the worker command prefix, worker requests) are wrapped in
  `literal()`.
- **Payload first, `package.py` last.** Write into a staging folder from `store.staging_dir`,
  rename it into place with `store.move_into_place`, and write `package.py` with
  `store.write_text_atomic` after the files it describes exist.
- **Ranges, not pins.** Package `requires` come from the version ranges in `pyproject.toml` and
  manifests, converted by `versions.rez_requirement`. A specifier that cannot be converted fails
  the build. The only pins are the `gtn` app and editor in the engine package, taken from the
  resolution that installed them.
- **A build that cannot be checked has failed.** Each command resolves what it wrote with rez
  and exits non-zero when that fails. `--yes` only skips prompts.
- **No machine-dependent build inputs.** `--torch-backend=auto` and `UV_TORCH_BACKEND` are
  refused; torch builds are explicit `--torch-backend` variants and each machine picks its own
  with `implicit_packages` (see `gpu-config`).

## Code Style Preferences

**Avoid Tuples For Return Values** - Tuples should be a last resort. When unavoidable, use NamedTuples for clarity. Prefer separate variables, class instances, or other data structures.

**Simple, Readable Logic Flow** - Prefer simple, easy-to-follow logic over complex nested expressions. Use explicit if/else statements instead of ternary operators or nested conditionals. Break complex nested expressions into clear, separate statements.

**Evaluate ALL failure cases first, success path ONLY at the end** - ALL validation checks, error conditions, and failure cases must be at the top of the function. Each failure case should exit immediately (return/raise). The success path must be at the absolute bottom of the function.

**Do NOT use lazy imports** - All imports must be at the top of the file. Never import inside functions unless it is the only way to resolve a circular import. If a lazy import is required, add a comment explaining which circular dependency makes it necessary.

**Class organization order** - Organize class members in this order:

1. Class attributes
1. `__init__`
1. Other dunder methods
1. Properties
1. Public instance methods
1. Private instance methods
1. Class methods
1. Static methods

Within each group, put high-level methods first and helper methods below the callers that use them.

## Exception Handling

**Only wrap code that actually raises exceptions** - Verify that code raises exceptions before adding try/except. Do not add try/except blocks speculatively.

**Use specific, narrow exception blocks** - Catch only the specific exception types that can be raised. Keep try blocks as small as possible. Never use bare `except:` or catch `Exception`.

**Write administrator-readable errors** - Failures are raised as `BuildError` and printed by the CLI, which exits with status 1. Use the format "Attempted to do X. Failed because Y." and say what to do next when there is something to do. Avoid internal type names and stack-trace jargon.
