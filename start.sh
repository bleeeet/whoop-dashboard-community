#!/bin/sh
# WorkBuddy sites deploy entrypoint (python http-service).
#
# DATA_ROOT must point outside the source tree: every redeploy replaces the source
# directory, so state kept next to cloud_server.py would be wiped. The sandbox reuses
# the same container across deploys, so /tmp/whoop-app-data survives them.
#
# Pointing DATA_ROOT at a new directory is also the "start over" switch: the old
# directory stays in the sandbox, so naming it back brings the old state. Inside a
# running site, 设置 → 恢复出厂设置 does the same thing without a redeploy.
#
# The public origin is derived at runtime from the gateway's X-Wb-Base-Url header,
# so PUBLIC_URL normally does not need to be set. Set it only to override.
if [ -z "$DATA_ROOT" ]; then
    if mkdir -p /tmp/whoop-app-data 2>/dev/null; then
        DATA_ROOT=/tmp/whoop-app-data
    else
        DATA_ROOT="$(cd "$(dirname "$0")" && pwd)/runtime"
        mkdir -p "$DATA_ROOT"
    fi
fi
export DATA_ROOT
echo "WHOOP starting PORT=${PORT:-8080} DATA_ROOT=$DATA_ROOT PUBLIC_URL=${PUBLIC_URL:-<auto>}"
exec python3 cloud_server.py
