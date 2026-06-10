# Backup sidecar: reuses the Neo4j image (ships cypher-shell + neo4j-admin —
# no extra tooling to maintain) and runs backup.sh on a cron-like loop.
ARG NEO4J_IMAGE=neo4j:5.26-community
FROM ${NEO4J_IMAGE}

USER root
COPY backup.sh /backup.sh
RUN chmod +x /backup.sh

# BACKUP_INTERVAL_SECONDS defaults to nightly. The first run happens one
# interval after start (so a crash-looping sidecar can't hammer the DB).
ENTRYPOINT ["/bin/sh", "-c", "while true; do sleep ${BACKUP_INTERVAL_SECONDS:-86400}; /backup.sh || echo '[backup] FAILED (will retry next interval)'; done"]
