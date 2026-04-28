#!/bin/sh

echo "Starting RQ worker"
echo "REDIS_URL=${REDIS_URL:-<missing>}"

exec rq worker --url "${REDIS_URL}" import scrape
