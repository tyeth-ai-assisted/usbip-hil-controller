#!/usr/bin/env bash
# Configure dual-push on the protomq submodule so commits land on both
# the tyeth-ai-assisted fork (default origin) and the tyeth/protomq
# personal upstream. Run once after `git submodule update --init`.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

if [[ ! -d vendor/protomq/.git || -f vendor/protomq/.git ]]; then
    # Submodules use a gitlink file pointing at the parent's .git/modules/.
    # Either layout is fine; just make sure the submodule has been initialised.
    if ! git submodule status vendor/protomq >/dev/null 2>&1; then
        echo "vendor/protomq is not initialised. Run: git submodule update --init --recursive" >&2
        exit 1
    fi
fi

primary="https://github.com/tyeth-ai-assisted/protomq.git"
mirror="https://github.com/tyeth/protomq.git"

# `set-url --push` first replaces the (implicit) pushurl with $primary,
# then we append $mirror so `git push origin` writes to both.
git -C vendor/protomq remote set-url --push origin "$primary"
git -C vendor/protomq remote set-url --add --push origin "$mirror"

echo "vendor/protomq origin push URLs:"
git -C vendor/protomq remote get-url --push --all origin | sed 's/^/  /'
echo
echo "Done. \`git push\` from inside vendor/protomq will now reach both forks."
