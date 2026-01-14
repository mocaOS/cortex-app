#!/bin/bash
# Wrapper entrypoint script for Neo4j to filter out NEO4J_URI
# This prevents Neo4j from trying to parse NEO4J_URI as a config setting
# when it's passed as a global environment variable from Coolify

# Unset NEO4J_URI if it exists (it's only for backend connection, not Neo4j config)
unset NEO4J_URI

# Call the original Neo4j entrypoint with all arguments
exec /startup/docker-entrypoint.sh neo4j "$@"
