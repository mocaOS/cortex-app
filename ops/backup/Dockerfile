# Backup sidecar: reuses the Neo4j image (ships cypher-shell + neo4j-admin —
# no extra tooling to maintain) and runs backup.sh on a cron-like loop.
ARG NEO4J_IMAGE=neo4j:5.26-community
FROM ${NEO4J_IMAGE}

USER root
COPY backup.sh restore.sh /
RUN chmod +x /backup.sh /restore.sh

# First backup runs BACKUP_INITIAL_DELAY_SECONDS after start (default 120s —
# long enough for neo4j to come up, short enough that a fresh stack isn't
# unprotected for a whole interval), then every BACKUP_INTERVAL_SECONDS.
# Failures echo and retry next interval; the compose healthcheck watches
# /backups/LAST_SUCCESS freshness so a silently failing backup goes unhealthy.
ENTRYPOINT ["/bin/bash", "-c", "sleep ${BACKUP_INITIAL_DELAY_SECONDS:-120}; while true; do /backup.sh || echo '[backup] FAILED (will retry next interval)'; sleep ${BACKUP_INTERVAL_SECONDS:-86400}; done"]
