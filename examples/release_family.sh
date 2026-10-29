#!/usr/bin/env bash
# Promote one Rez family and its resolved dependencies from the local store to the
# release store. An example to adapt: see docs/releasing.md.
# usage: release_family.sh FAMILY LOCAL_STORE RELEASE_STORE "PRODUCTION_PATHS" [--dry-run]
#   PRODUCTION_PATHS: the package search path artists use, WITHOUT the local store
#   (release store + studio stores holding python/platform/os/arch), ':'-separated (';' on Windows).
#   EXTRA_REQUESTS (environment, optional): more requests for the resolve, e.g. a torch
#   build ".torch_backend-cu118", so that build's packages are released too.
set -euo pipefail
FAMILY="$1"; LOCAL="$2"; RELEASE="$3"; PRODUCTION_PATHS="$4"; DRY_RUN="${5:-}"
EXTRA_REQUESTS="${EXTRA_REQUESTS:-}"
# Rez separates search paths like the platform does: ';' on Windows (where drive letters hold ':').
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) SEP=';' ;;
  *) SEP=':' ;;
esac
mkdir -p "$RELEASE"
# Resolves are written to a context file and read with rez-context, so no Python code goes
# through rez-env's command line (rez-env on Windows strips the double quotes inline code needs).
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# 1. Resolve the family the way artists will, but with the local store first.
# shellcheck disable=SC2086  # EXTRA_REQUESTS is a space-separated list of requests
rez-env "$FAMILY" $EXTRA_REQUESTS --paths "$LOCAL$SEP$PRODUCTION_PATHS" --output "$WORK/local.rxt"
RESOLVE=$(rez-context --print-resolve "$WORK/local.rxt")

# 2. Copy each resolved package that came from the local store and is not yet released.
for spec in $RESOLVE; do
  name="${spec%%-*}"; version="${spec#*-}"   # family names never contain '-'; versions can
  [ -d "$LOCAL/$name/$version" ] || continue  # studio package, not ours to release
  if [ -d "$RELEASE/$name/$version" ]; then echo "already released: $spec"; continue; fi
  # rez-cp --dry-run fails on packages without variants (rez 3.4.0), so a dry run only lists them.
  if [ -n "$DRY_RUN" ]; then echo "would copy: $spec"; continue; fi
  rez-cp "$spec" --paths "$LOCAL" --dest-path "$RELEASE" --allow-empty --keep-timestamp
done

# 3. Validate: the family must resolve without the local store.
if [ -z "$DRY_RUN" ]; then
  # shellcheck disable=SC2086
  rez-env "$FAMILY" $EXTRA_REQUESTS --paths "$PRODUCTION_PATHS" --output "$WORK/production.rxt"
  echo "$FAMILY resolves without the local store"
fi
