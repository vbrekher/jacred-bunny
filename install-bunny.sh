#!/bin/sh
set -eu
command -v curl >/dev/null 2>&1 || { echo 'curl is required' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9+ is required' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo 'Python 3.9+ required' >&2; exit 1; }
REPO='vbrekher/jacred-bunny'
BIN_DIR="${JACRED_BUNNY_BIN_DIR:-$HOME/.local/bin}"
SHARE_DIR="${JACRED_BUNNY_SHARE_DIR:-$HOME/.local/share/jacred-bunny}"
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
python3 - "$TMP" "$SHARE_DIR" "$REV" "$BIN_DIR/jacred-bunny" <<'PYINSTALL'
from pathlib import Path
import hashlib, os, shlex, shutil, sys, tempfile
source, share, revision, wrapper = map(Path, (sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]))
release = str(revision)
versions = share / "versions"
if share.is_symlink() or versions.is_symlink() or wrapper.is_symlink():
    raise SystemExit("Unsafe installation path")
versions.mkdir(mode=0o700,parents=True,exist_ok=True)
destination = versions/release
modules = {"cli.py":source,"native.py":Path(str(source)+".native"),
 "operations.py":Path(str(source)+".operations"),"backup.py":Path(str(source)+".backup"),
 "legacy.py":Path(str(source)+".legacy")}
if destination.exists() or destination.is_symlink():
    if destination.is_symlink() or not destination.is_dir():
        raise SystemExit("Invalid immutable CLI version")
    for name, original in modules.items():
        target = destination/name
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(original.read_bytes()).digest():
            raise SystemExit("Installed CLI version differs; refusing overwrite")
else:
    with tempfile.TemporaryDirectory(prefix=".install-",dir=versions) as temporary:
        staged = Path(temporary)/"source"
        staged.mkdir(mode=0o700)
        for name, original in modules.items():
            target=staged/name
            shutil.copyfile(original,target)
            target.chmod(0o600)
        os.rename(staged,destination)
# Switch only the tiny wrapper after the complete version is available.
fd, name = tempfile.mkstemp(prefix=".jacred-wrapper-",dir=wrapper.parent)
try:
    with os.fdopen(fd,"w") as output:
        output.write("#!/bin/sh\nexec python3 "+shlex.quote(str(destination/"cli.py"))+' "$@"\n')
        output.flush();os.fsync(output.fileno())
    os.chmod(name,0o755)
    if wrapper.is_symlink():raise SystemExit("Wrapper became a symlink")
    os.replace(name,wrapper)
finally:
    if os.path.exists(name):os.unlink(name)
PYINSTALL
printf 'Installed %s/jacred-bunny\n' "$BIN_DIR"
if [ "$#" -eq 0 ]; then set -- install; fi
"$BIN_DIR/jacred-bunny" "$@"
