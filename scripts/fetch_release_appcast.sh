#!/usr/bin/env bash
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Fetch the latest published release feed for a candidate tagged release.
# Keep release history rooted in the published asset, never in the seed file.
# Authenticate the complete XML with the application's embedded public key.
# Allow the tracked seed only when this is the repository's first version tag.
# Fail before release assembly if history is missing or has been altered.
# The Makefile invokes this for both release preflight and appcast assembly.

set -euo pipefail

candidate_tag=$1
output=$2
repository=${GITHUB_REPOSITORY:-simsong/email-collection-toolkit}
previous_tag=$(gh api "repos/$repository/releases?per_page=100" \
  --jq '[.[] | select(.draft == false)][0].tag_name // empty')

if [[ -z "$previous_tag" ]]; then
  if [[ "$(git tag --list 'v*')" != "$candidate_tag" ]]; then
    echo 'No previous published appcast is available; refusing to reset update history' >&2
    exit 1
  fi
  cp website/static/updates/mac/appcast.xml "$output"
  make check-appcast APPCAST="$output"
  exit 0
fi

for attempt in 1 2 3 4 5 6; do
  if gh release download "$previous_tag" --repo "$repository" \
    --pattern appcast.xml --output "$output" --clobber; then
    break
  fi
  if [[ "$attempt" -eq 6 ]]; then exit 1; fi
  sleep "$attempt"
done
make check-appcast APPCAST="$output" RELEASE_TAG="$previous_tag" ARGS=--require-signed-feed
