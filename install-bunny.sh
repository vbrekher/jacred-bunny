#!/bin/sh
set -eu
command -v curl >/dev/null 2>&1 || { echo 'curl is required' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9+ is required' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo 'Python 3.9+ required' >&2; exit 1; }
REPO='vbrekher/jacred-bunny'
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/jacred-bunny"
mkdir -p "$BIN_DIR" "$SHARE_DIR"
REV="$(curl -fsSL --retry 3 -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/$REPO/commits/main" | \
  python3 -c 'import json,sys,re; s=json.load(sys.stdin).get("sha", "");print(s if re.fullmatch("[0-9a-f]{40}",s) else "")')"
[ -n "$REV" ] || { echo 'Cannot resolve trusted GitHub commit; aborting' >&2; exit 1; }
TMP_DIR="$(mktemp -d)"
TMP="$TMP_DIR/cli.py"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
printf 'Installing JacRed Bunny CLI at GitHub revision %s\n' "$REV"
curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$REV/bunny/cli.py" -o "$TMP"
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
required=["APP_NAME = \"jacred-bunny\"", "def ensure_registry(", "def ensure_no_duplicate(",
          "def create_app_safely(", "\"imageRegistryId\": str(registry)", "def self_test("]
if not all(x in s for x in required):
    raise SystemExit("Invalid JacRed Bunny CLI; refusing to install")
compile(s,sys.argv[1],"exec")
PY
curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$REV/bunny/native.py" -o "$TMP.native"
python3 - "$TMP.native" <<'PYTHON'
from pathlib import Path
import sys
compile(Path(sys.argv[1]).read_text(), sys.argv[1], "exec")
PYTHON
curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$REV/bunny/operations.py" -o "$TMP.operations"
python3 - "$TMP.operations" <<'PYTHON'
from pathlib import Path
import sys
compile(Path(sys.argv[1]).read_text(), sys.argv[1], "exec")
PYTHON
curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$REV/bunny/backup.py" -o "$TMP.backup"
python3 - "$TMP.backup" <<'PYTHON'
from pathlib import Path
import sys
compile(Path(sys.argv[1]).read_text(), sys.argv[1], "exec")
PYTHON
python3 "$TMP" self-test
curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$REV/bunny/legacy.py" -o "$TMP.legacy"
python3 - "$TMP.legacy" <<'PYTHON'
from pathlib import Path
import sys
compile(Path(sys.argv[1]).read_text(), sys.argv[1], "exec")
PYTHON
mv "$TMP.legacy" "$SHARE_DIR/legacy.py"
chmod 600 "$SHARE_DIR/legacy.py"
mv "$TMP.backup" "$SHARE_DIR/backup.py"
chmod 600 "$SHARE_DIR/backup.py"
mv "$TMP.operations" "$SHARE_DIR/operations.py"
chmod 600 "$SHARE_DIR/operations.py"
mv "$TMP.native" "$SHARE_DIR/native.py"
chmod 600 "$SHARE_DIR/native.py"
mv "$TMP" "$SHARE_DIR/cli.py"
chmod 600 "$SHARE_DIR/cli.py"
cat > "$BIN_DIR/jacred-bunny" <<'WRAPPER'
#!/bin/sh
exec python3 "$HOME/.local/share/jacred-bunny/cli.py" "$@"
WRAPPER
chmod 755 "$BIN_DIR/jacred-bunny"
printf 'Installed ~/.local/bin/jacred-bunny\n'
if [ "$#" -eq 0 ]; then set -- setup; fi
"$BIN_DIR/jacred-bunny" "$@"
