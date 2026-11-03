#!/bin/sh
# Strictly loopback-only allowlist. Read the current key after config rotation.
set -eu
case "$1" in
 checkpoint) path=/jsondb/save ;;
 resume) path=/cron/maintenance/ResumeParseAll ;;
 report) path='/cron/maintenance/Check?mode=report' ;;
 *) echo "Unknown maintenance job" >&2; exit 64 ;;
esac
config="${JACRED_BUNNY_CONFIG_FILE:-/app/config/init.yaml}"
# Managed Bunny keys are URL-safe ASCII, 24-128 chars. Fail closed on an
# unsupported/duplicate field instead of forwarding a truncated credential.
if ! dev="$(awk '
  /^devkey:/ {
    count++; value=substr($0,8)
    sub(/^[ \t]*/, "", value); sub(/[ \t]*#.*/, "", value); sub(/[ \t]*$/, "", value)
    quote=substr(value,1,1)
    if ((quote=="\"" || quote==sprintf("%c",39)) && substr(value,length(value),1)==quote)
      value=substr(value,2,length(value)-2)
    if (value !~ /^[A-Za-z0-9_-]+$/ || length(value)<24 || length(value)>128) bad=1
    else key=value
  }
  END { if(count!=1 || bad || key=="") exit 1; print key }
' "$config")"; then
  echo "Missing or invalid managed administrative key in config" >&2
  exit 1
fi
# The key travels over stdin, never in argv or the URL. No system proxy or
# redirects may send this loopback administrative credential elsewhere.
printf 'header = "X-Dev-Key: %s"\n' "$dev" |
  curl --config - --noproxy '*' --proto '=http' -fsS --connect-timeout 3 --max-time 50 "http://127.0.0.1:9117$path" >/dev/null
