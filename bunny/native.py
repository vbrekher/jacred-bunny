"""Journalled Bunny-native staging provisioning. Standard library only.

This control plane creates infrastructure; it never labels an empty catalog
production ready. Existing FileDB and the legacy CLI remain supported.
"""
from datetime import datetime, timezone
import hashlib
import fcntl
import functools
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request

OWNER = "vbrekher/jacred-bunny"
CREATED_BY = "jacred-bunny-native-cli"


class NativeError(Exception):
    pass


def atomic_private(path, value):
    path = Path(path)
    if path.is_symlink():
        raise NativeError("Refusing symlink state")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_private(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise NativeError("Expected private mode-600 regular state file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise NativeError("Cannot read deployment state") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must never receive an account AccessKey.
        raise NativeError("Unexpected API redirect")


class API:
    def __init__(self, key):
        self.key = key
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, method, path, body=None):
        if not re.fullmatch(r"/(?:mc|database|compute|storagezone|pullzone)(?:/[A-Za-z0-9_.?=&%/-]+)?", path) or ".." in path:
            raise NativeError("Unsafe control-plane API path")
        req = urllib.request.Request("https://api.bunny.net" + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"AccessKey": self.key, "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise NativeError("API response exceeds limit")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Bodies can echo credentials; intentionally omit them.
            raise NativeError("Bunny HTTP %d (%s %s)" % (exc.code, method, path.split("?")[0])) from None
        except (urllib.error.URLError, TimeoutError):
            raise NativeError("Bunny API network failure") from None


def sql_statements(text):
    """Use SQLite's parser to preserve trigger bodies, comments and literals."""
    pending = ""
    for char in text:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip() and not all(not line.strip() or line.lstrip().startswith("--") for line in pending.splitlines()):
        raise NativeError("Incomplete migration SQL")


def serialized(method):
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.directory / ".deployment.lock"
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NativeError("Another deployment command is running") from None
            if self.path.exists():
                self.state = read_private(self.path)
                if self.state.get("owner") != OWNER or self.state.get("created_by") != CREATED_BY:
                    raise NativeError("Deployment owner mismatch")
            return method(self, *args, **kwargs)
        finally:
            os.close(fd)
    return locked


class Deployment:
    def __init__(self, directory, api):
        self.directory = Path(directory)
        self.path = self.directory / "deployment.json"
        self.secret_path = self.directory / "credentials.json"
        self.api = api
        self.state = read_private(self.path) if self.path.exists() else None
        if self.state and (self.state.get("owner") != OWNER or self.state.get("created_by") != CREATED_BY):
            raise NativeError("Deployment owner mismatch")

    def save(self):
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_private(self.path, self.state)

    @serialized
    def initialize(self, source_sha, image, digest, region="AMS", hours=1):
        if self.state:
            if self.state["source_sha"] != source_sha:
                raise NativeError("Existing deployment uses another source; use update")
            return
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise NativeError("Expected exact source commit SHA")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise NativeError("Expected verified immutable image digest")
        if not 0 < hours <= 24:
            raise NativeError("Staging expiry must be within 24 hours")
        suffix = secrets.token_hex(4)
        self.state = {"owner": OWNER, "created_by": CREATED_BY, "source_sha": source_sha,
                      "image": image, "image_digest": digest, "region": region,
                      "name": "jacred-" + suffix, "resources": {}, "phase": "planned",
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "stop_at": time.time() + hours * 3600, "production_ready": False,
                      "migration_state": "not_applied", "hard_cost_limit": False}
        atomic_private(self.secret_path, {"api_key": secrets.token_urlsafe(36),
                                          "admin_key": secrets.token_urlsafe(36)})
        self.save()

    def create(self, kind, path, body, id_field, get_path, name_field, response_field=None):
        """Persist intent before POST; ambiguous creation never causes a replay."""
        old = self.state["resources"].get(kind)
        if old:
            if old.get("status") == "creating":
                raise NativeError("Uncertain %s creation; reconcile account before resume" % kind)
            self.verify(kind)
            return old["id"]
        record = {"name": self.state["name"], "get_path": get_path,
                  "name_field": name_field, "response_field": response_field, "status": "creating"}
        self.state["resources"][kind] = record
        self.save()
        result = self.api("POST", path, body)
        ident = result.get(id_field) if isinstance(result, dict) else None
        if type(ident) not in (str, int) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(ident)):
            raise NativeError("Create response omitted ID; do not replay POST")
        record.update(id=ident, status="created")
        self.save()
        self.verify(kind)
        return ident

    def verify(self, kind):
        record = self.state["resources"][kind]
        if record.get("status") != "created":
            raise NativeError("Resource identity is not confirmed")
        remote = self.api("GET", record["get_path"].format(id=record["id"]))
        if record.get("response_field"):
            remote = remote.get(record["response_field"]) if isinstance(remote, dict) else None
        if not isinstance(remote, dict) or remote.get(record["name_field"]) != record["name"]:
            raise NativeError("Remote %s identity changed; refusing mutation" % kind)
        return remote

    @serialized
    def provision_data_plane(self):
        name = self.state["name"]
        # Actual API enforces <=16, despite OpenAPI's <64 description.
        if len(name) > 16:
            raise NativeError("Database name exceeds live API limit")
        db = self.create("database", "/database/v2/databases", {
            "name": name, "storage_region": "eu-west-1", "primary_regions": [self.state["region"]],
            "replicas_regions": []}, "db_id", "/database/v2/databases/{id}", "name", "db")
        storage = self.create("storage", "/storagezone", {
            "Name": name, "Region": "DE", "ReplicationRegions": [], "ZoneTier": 0},
            "Id", "/storagezone/{id}", "Name")
        edge = self.create("edge", "/compute/script", {
            "Name": name, "ScriptType": 1, "CreateLinkedPullZone": True, "LinkedPullZoneName": name},
            "Id", "/compute/script/{id}", "Name")
        self.state["phase"] = "data_plane_created"
        self.save()
        return {"database": db, "storage": storage, "edge": edge}

    @serialized
    def wire_edge(self, bundle, expected_sha256):
        code = Path(bundle).read_bytes()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or hashlib.sha256(code).hexdigest() != expected_sha256:
            raise NativeError("Edge bundle checksum mismatch")
        db = self.verify("database")
        edge = self.verify("edge")
        credentials = read_private(self.secret_path)
        ident = self.state["resources"]["database"]["id"]
        if "database_read_token" not in credentials:
            token = self.api("PUT", "/database/v2/databases/" + str(ident) + "/auth/generate",
                             {"authorization": "read-only", "expires_at": None})
            if not isinstance(token, dict) or not isinstance(token.get("token"), str):
                raise NativeError("Missing database read token")
            credentials["database_read_token"] = token["token"]
            atomic_private(self.secret_path, credentials)
        eid = self.state["resources"]["edge"]["id"]
        values = {"BUNNY_DATABASE_URL": db["url"], "BUNNY_DATABASE_AUTH_TOKEN": credentials["database_read_token"],
                  "JACRED_EDGE_API_KEY": credentials["api_key"], "JACRED_EDGE_PREVIEW_ENABLED": "true"}
        for name, value in values.items():
            self.api("PUT", "/compute/script/%s/secrets" % eid, {"Name": name, "Secret": value})
        self.api("POST", "/compute/script/%s/code" % eid, {"Code": code.decode("utf-8")})
        self.api("POST", "/compute/script/%s/publish" % eid, {"Note": "JacRed staging " + self.state["source_sha"]})
        self.state["edge_bundle_sha256"] = expected_sha256
        self.state["edge_url"] = edge.get("DefaultHostname")
        self.state["phase"] = "edge_published_catalog_not_ready"
        self.save()

    def status(self):
        result = {"owner": OWNER, "phase": self.state["phase"], "production_ready": False, "resources": {}}
        for kind in self.state["resources"]:
            remote = self.verify(kind)
            record = self.state["resources"][kind]
            result["resources"][kind] = {"id": record["id"], "name": record["name"],
                                         "status": remote.get("status", "identity_verified")}
        return result
