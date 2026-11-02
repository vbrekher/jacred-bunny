#!/bin/sh
# Strictly loopback-only allowlist. Never accepts arbitrary URLs.
set -eu
case "$1" in
 checkpoint) path=/jsondb/save ;;
 resume) path=/cron/maintenance/ResumeParseAll ;;
 report) path='/cron/maintenance/Check?mode=report' ;;
 *) echo "Unknown maintenance job" >&2; exit 64 ;;
esac
curl -fsS --connect-timeout 3 --max-time 50 "http://127.0.0.1:9117$path" >/dev/null
