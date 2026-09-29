#!/usr/bin/env bash
# Promote one Rez family and its resolved dependencies from the local store to the
# release store. An example to adapt: see docs/releasing.md.
# usage: release_family.sh FAMILY LOCAL_STORE RELEASE_STORE "PRODUCTION_PATHS" [--dry-run]
#   PRODUCTION_PATHS: the package search path artists use, WITHOUT the local store
#   (release store + studio stores holding python/platform/os/arch), ':'-separated.
#   EXTRA_REQUESTS (environment, optional): more requests for the resolve, e.g. a torch
#   build ".torch_backend-cu118", so that build's packages are released too.
set -euo pipefail
FAMILY="$1"; LOCAL="$2"; RELEASE="$3"; PRODUCTION_PATHS="$4"; DRY_RUN="${5:-}"
EXTRA_REQUESTS="${EXTRA_REQUESTS:-}"
mkdir -p "$RELEASE"

# 1. Resolve the family the way artists will, but with the local store first.
# shellcheck disable=SC2086  # EXTRA_REQUESTS is a space-separated list of requests
RESOLVE=$(rez-env "$FAMILY" $EXTRA_REQUESTS --paths "$LOCAL:$PRODUCTION_PATHS" -- \
  python -c "import os; print(os.environ['REZ_USED_RESOLVE'])")

# 2. Copy each resolved package that came from the local store and is not yet released.
for spec in $RESOLVE; do
  name="${spec%%-*}"; version="${spec#*-}"   # family names never contain '-'; versions can
  [ -d "$LOCAL/$name/$version" ] || continue  # studio package, not ours to release
  if [ -d "$RELEASE/$name/$version" ]; then echo "already released: $spec"; continue; fi
  rez-cp "$spec" --paths "$LOCAL" --dest-path "$RELEASE" --allow-empty --keep-timestamp $DRY_RUN
done

# 3. Validate: the family must resolve without the local store.
if [ -z "$DRY_RUN" ]; then
  # shellcheck disable=SC2086
  rez-env "$FAMILY" $EXTRA_REQUESTS --paths "$PRODUCTION_PATHS" -- python -c "print('resolves without the local store')"
fi
