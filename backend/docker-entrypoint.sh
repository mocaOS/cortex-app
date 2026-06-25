#!/bin/sh
# Production entrypoint: fix volume ownership, then drop to the non-root app user.
#
# Orchestrators (Dokploy, Coolify, plain `docker compose` with named volumes)
# back the writable mounts with host dirs that mount *root-owned*. That mount
# shadows the image's build-time `chown appuser /app`, so the app — which runs
# as UID 1000 — gets EACCES on the first write:
#   - uploads            -> POST /api/upload
#   - .agents/skills     -> downloading/installing a skill ([Errno 13] .../skills/<id>)
#   - custom_inputs      -> manual Q&A / text / markdown inputs
#   - .cache/huggingface -> reranker/embedding model download
#
# A build-time chown can't fix this (the volume mount happens at runtime, after
# the image is built). So we start as root, chown the mounts to 1000:1000, and
# exec the real command via gosu. This self-heals on every start regardless of
# how the volume was provisioned.
set -e

APP_UID=1000
APP_GID=1000

for d in /app/uploads /app/custom_inputs /app/.agents/skills /app/.cache/huggingface; do
    mkdir -p "$d"
    # Skip the recursive chown when the mount already has the right owner. This
    # keeps restarts fast — otherwise we'd re-walk the (potentially large) HF
    # model cache every boot. A freshly provisioned root-owned volume trips the
    # branch once, then stays owned by 1000 on subsequent starts.
    if [ "$(stat -c '%u' "$d")" != "$APP_UID" ]; then
        echo "entrypoint: fixing ownership of $d -> $APP_UID:$APP_GID"
        chown -R "$APP_UID:$APP_GID" "$d"
    fi
done

# Drop root and exec the CMD (passed as "$@") as the non-root app user. exec so
# the app becomes PID 1 and receives SIGTERM directly for graceful shutdown.
exec gosu "$APP_UID:$APP_GID" "$@"
