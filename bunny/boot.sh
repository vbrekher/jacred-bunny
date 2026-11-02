#!/bin/sh
# Bunny-specific boot: use persistent config, require first-run secrets.
set -eu
umask 077
fail() { echo "JacRed Bunny: $*" >&2; exit 1; }
valid_key() {
  count="$(printf %s "$1" | wc -c | tr -d ' ')"
  [ "$count" -ge 32 ] && [ "$count" -le 128 ] || return 1
  case "$1" in *[!A-Za-z0-9_-]*) return 1 ;; esac
}
# Persistent volumes can arrive owned by root. Correct just the mount roots at startup.
# Do not recursively chown a potentially multi-GB FileDB on every restart.
mkdir -p /app/config /app/Data
chown jacred:jacred /app/config /app/Data
chmod 700 /app/config
su-exec jacred:jacred mkdir -p /app/Data/temp

if [ ! -s /app/config/init.yaml ] && [ ! -s /app/config/init.conf ]; then
  api="$(printenv JACRED_BUNNY_API_KEY || true)"
  dev="$(printenv JACRED_BUNNY_DEV_KEY || true)"
  valid_key "$api" || fail "Set JACRED_BUNNY_API_KEY: 32-128 letters, digits, - or _"
  valid_key "$dev" || fail "Set JACRED_BUNNY_DEV_KEY: 32-128 letters, digits, - or _"
  [ "$api" != "$dev" ] || fail "API and admin keys must differ"
  [ -f /app/defaults/init.yaml ] || fail "Missing packaged default config"
  cp /app/defaults/init.yaml /app/config/init.yaml
  sed -i \
    -e "s/^apikey:.*/apikey: \"$api\"/" \
    -e "s/^devkey:.*/devkey: \"$dev\"/" \
    -e '/^flaresolverr:/,/^cffetch:/ s/^  enable: true/  enable: false/' \
    -e '/^cffetch:/,/^mergeduplicates:/ s/^  enable: true/  enable: false/' \
    /app/config/init.yaml
  chown jacred:jacred /app/config/init.yaml
  chmod 600 /app/config/init.yaml
  case "${JACRED_BUNNY_SYNC_API:-}" in
    '') ;;
    'https://sync.jacred.stream')
      sed -i 's|^syncapi:.*|syncapi: "https://sync.jacred.stream"|' /app/config/init.yaml
      echo "JacRed Bunny: official remote sync enabled on first boot"
      ;;
    *) fail "Unsupported sync API supplied; refusing to write arbitrary remote URL" ;;
  esac
  echo "JacRed Bunny: secured configuration created on persistent volume"
fi
[ -s /app/config/init.yaml ] || fail "Bunny profile requires persistent /app/config/init.yaml"
for name in apikey devkey; do
  awk -v field="$name" '
    index($0, field ":") == 1 {
      v=substr($0, length(field)+2)
      sub(/[[:space:]]*#.*/, "", v)
      gsub(/[^A-Za-z0-9_-]/, "", v)
      if (length(v)>=24) valid=1
    }
    END { exit(valid?0:1) }
  ' /app/config/init.yaml || fail "Missing/short $name in persistent config"
done
case "$JACRED_BUNNY_CRON" in
  0) ;;
  1)
    echo "JacRed Bunny: enabling local maintenance-only cron"
    crond -c /app/bunny/crontabs -l 6 || fail "Unable to start crond"
    ;;
  *) fail "JACRED_BUNNY_CRON must be 0 or 1" ;;
esac
exec su-exec jacred:jacred /entrypoint.sh "$@"
