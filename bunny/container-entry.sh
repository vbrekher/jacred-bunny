#!/bin/sh
# Image-owned root bootstrap. Source volumes are never chowned for export.
set -eu
umask 077
case "${JACRED_LEGACY_EXPORT:-0}" in
  1)
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
    handoff="$(mktemp -d /tmp/jacred-legacy-handoff.XXXXXXXXXX)"
    trap 'rm -rf -- "$handoff"' EXIT
    /usr/local/libexec/jacred-export legacy-export-prepare "$handoff"
    # Transfer only the freshly generated private artifacts, directory last.
    chown -h jacred:jacred "$handoff/legacy.tar.gz" "$handoff/prepared.json"
    chown -h jacred:jacred "$handoff"
    # Keep this bootstrap for cleanup; dumb-init forwards signals to the group.
    set +e
    interrupted=0
    trap 'interrupted=1' TERM INT
    /sbin/su-exec jacred:jacred /app/JacRed legacy-export-serve "$handoff" &
    child=$!
    wait "$child"
    result=$?
    if [ "$interrupted" -eq 1 ] && [ "$result" -ge 128 ]; then
      wait "$child"
      result=$?
    fi
    exit "$result"
    ;;
  *) exec /usr/local/libexec/jacred-bunny-boot "$@" ;;
esac
