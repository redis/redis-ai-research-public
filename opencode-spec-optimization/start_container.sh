#!/bin/sh
set -eu

host_workspace="${HOST_WORKSPACE:-/workspace}"
merged_workspace="${MERGED_WORKSPACE:-/tmp/opencode-workspace}"

rm -rf "$merged_workspace"
mkdir -p "$merged_workspace"

if [ -d "$host_workspace" ]; then
    for entry in "$host_workspace"/* "$host_workspace"/.[!.]* "$host_workspace"/..?*; do
        if [ ! -e "$entry" ]; then
            continue
        fi
        name=$(basename "$entry")
        ln -s "$entry" "$merged_workspace/$name"
    done
fi

cp /opt/opencode-spec-optimization/main.py "$merged_workspace/main.py"

for name in diagnostics mcps opencode.jsonc; do
    if [ ! -e "$merged_workspace/$name" ] && [ -e "/opt/opencode-spec-optimization/$name" ]; then
        ln -s "/opt/opencode-spec-optimization/$name" "$merged_workspace/$name"
    fi
done

export APP_ROOT="$merged_workspace"
cd "$merged_workspace"

exec uv run --project /opt/opencode-spec-optimization --no-sync python "$merged_workspace/main.py" "$@"
