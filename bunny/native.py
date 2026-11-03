"""Journalled Bunny-native staging provisioning. Standard library only.

This control plane creates infrastructure; it never labels an empty catalog
production ready. Existing FileDB and the legacy CLI remain supported.
"""
from datetime import datetime, timezone
import hashlib
import html
import fcntl
import functools
from contextlib import contextmanager
import json
import base64
import math
import os
from pathlib import Path
import re
import secrets
import sys
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse

OWNER = "vbrekher/jacred-bunny"
CREATED_BY = "jacred-bunny-native-cli"


class NativeError(Exception):
    pass


def atomic_private(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
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
        if not re.fullmatch(r"/(?:mc|database|compute|storagezone|pullzone|user)(?:/[A-Za-z0-9_.?=&%/-]+)?", path) or ".." in path:
            raise NativeError("Unsafe control-plane API path")
        if path.startswith("/user") and (path != "/user" or method != "GET"):
            raise NativeError("Account writes are not supported")
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
        except (ValueError, TypeError):
            raise NativeError("Malformed Bunny API response") from None


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    ALLOWED = {"bunny.net", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "raw.githubusercontent.com"}

    @classmethod
    def validate(cls, url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in cls.ALLOWED or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise NativeError("Unexpected public artifact host")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url, limit=2*1024*1024):
    PublicRedirect.validate(url)
    try:
        with urllib.request.build_opener(PublicRedirect()).open(url, timeout=30) as response:
            value = response.read(limit+1)
        if len(value) > limit:
            raise NativeError("Public artifact exceeds size limit")
        return value
    except (urllib.error.URLError, TimeoutError):
        raise NativeError("Cannot fetch public artifact; no paid resources should be created") from None


def parse_pricing(mc, storage, scripting, database):
    mc, storage, scripting = (x.replace("\\", "") for x in (mc, storage, scripting))
    def price(text, pattern):
        match = re.search(pattern, text, re.I)
        if not match:
            raise NativeError("COST_UNKNOWN: published pricing format changed")
        result = float(match.group(1))
        if not math.isfinite(result) or result <= 0:
            raise NativeError("COST_UNKNOWN: invalid published rate")
        return result
    rates = {
        "cpu": price(mc, r"CPU cost:\s*\*\*\$([0-9.]+) per CPU per hour"),
        "ram": price(mc, r"RAM Cost:\s*\$([0-9.]+) per GB per hour"),
        "volume": price(mc, r"Storage cost:\s*\*\*\$([0-9.]+) per GB per month"),
        "storage_minimum": price(storage, r"\$([0-9.]+) monthly minimum"),
    }
    cpu_inc = price(scripting, r"\$([0-9.]+) / 1000s CPU time")
    req_inc = price(scripting, r"\$([0-9.]+) / million requests")
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", database)))
    if not re.search(r"While in public preview, Bunny Database is free", plain, re.I):
        raise NativeError("COST_UNKNOWN: Database public-preview pricing must be reviewed")
    rates["edge_increment"] = cpu_inc + req_inc
    return rates


def current_pricing():
    paths = ["https://bunny.net/docs/magic-containers/pricing.md", "https://bunny.net/docs/storage/pricing.md",
             "https://bunny.net/docs/scripting/pricing.md", "https://bunny.net/database/"]
    pages = [fetch_public(url).decode("utf-8") for url in paths]
    rates = parse_pricing(*pages)
    rates["verified_at"] = datetime.now(timezone.utc).isoformat()
    rates["source_sha256"] = {url: hashlib.sha256(page.encode()).hexdigest() for url, page in zip(paths, pages)}
    return rates


def staging_cost_plan(limits, hours=1, data_gb=1, rates=None):
    cpu = limits.get("maxCpuMilliCoresPerPod")
    memory = limits.get("maxMemoryBytesPerPod")
    if (type(cpu) not in (int, float) or type(memory) not in (int, float) or
        cpu <= 0 or memory <= 0 or not math.isfinite(cpu) or not math.isfinite(memory) or type(hours) not in (int, float) or not 0 < hours <= 24 or
        type(data_gb) is not int or not 1 <= data_gb <= 100):
        raise NativeError("COST_UNKNOWN: missing pod limits or invalid allocation")
    rates = rates or {"cpu": 0.02, "ram": 0.005, "volume": 0.10, "storage_minimum": 1.0, "edge_increment": 0.22}
    hourly = cpu / 1000 * rates["cpu"] + memory / (1024**3) * rates["ram"]
    volumes = (data_gb + 1) * rates["volume"]
    return {"compute_envelope_usd_per_hour": hourly, "hours": hours,
            "compute_envelope_usd": hourly * hours, "volumes_usd_per_month": volumes,
            "storage_minimum_usd": rates["storage_minimum"], "edge_first_increment_usd": rates["edge_increment"],
            "estimated_initial_usd": hourly * hours + volumes + rates["storage_minimum"] + rates["edge_increment"],
            "hard_cost_limit": False, "egress": "separately metered; not guaranteed by this estimate",
            "rates_verified_on": rates.get("verified_at", "2026-11-03"),
            "pricing_sources": ["https://bunny.net/docs/magic-containers/pricing.md",
                "https://bunny.net/docs/storage/pricing.md", "https://bunny.net/docs/scripting/pricing.md",
                "https://bunny.net/database/"]}


SQL_RUNTIME_MIGRATIONS = ("0001_catalog.sql", "0002_search_fts5.sql", "0004_catalog_commits.sql")


def environment_fingerprint(values):
    if (not isinstance(values, list) or any(not isinstance(row, dict) or set(row) != {"name", "value"} or
        not isinstance(row["name"], str) or not isinstance(row["value"], str) for row in values) or
        len({row["name"] for row in values}) != len(values)):
        raise NativeError("Invalid private worker environment shape")
    return hashlib.sha256(json.dumps(sorted(values,key=lambda row:row["name"]),sort_keys=True,separators=(",",":")).encode()).hexdigest()


def payload_spec(manifest):
    try:
        sha, image, files = manifest["source_sha"], manifest["image"], manifest["files"]
        schema = manifest.get("schema")
        if schema == "jacred-bunny-native-payload/v1":
            names = ("edge.js", *SQL_RUNTIME_MIGRATIONS[:2])
            if manifest.get("catalog_backend", "filedb") != "filedb":
                raise NativeError("Legacy payload cannot select SQL authority")
        elif schema == "jacred-bunny-native-payload/v2" and manifest.get("catalog_backend") == "bunny-sql":
            names = ("edge.js", *SQL_RUNTIME_MIGRATIONS)
        else:
            raise NativeError("Unsupported native payload schema/backend")
        if (not re.fullmatch(r"[0-9a-f]{40}", sha) or
            image["reference"] != "ghcr.io/vbrekher/jacred-bunny:sha-" + sha or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]) or
            not isinstance(files, dict) or set(files) != set(names) or
            any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in files.values())):
            raise NativeError("Invalid native payload identity")
        return names
    except (ValueError, TypeError, KeyError):
        raise NativeError("Invalid native payload manifest") from None


def load_payload(directory):
    directory = Path(directory)
    manifest_file = directory / "manifest.json"
    if manifest_file.is_symlink() or not manifest_file.is_file() or manifest_file.stat().st_size > 65536:
        raise NativeError("Missing or unsafe native payload manifest")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        names = payload_spec(manifest)
        for name in names:
            path = directory / name
            if (path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024 * 1024 or
                hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][name]):
                raise NativeError("Native payload file checksum mismatch")
        return manifest
    except (ValueError, TypeError, KeyError):
        raise NativeError("Invalid native payload manifest") from None


def fetch_release_payload(tag, cache, expected_source=None):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", tag):
        raise NativeError("Expected a versioned release tag")
    cache = Path(cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = cache / tag
    if target.is_symlink():
        raise NativeError("Unsafe release cache symlink")
    if target.is_dir():
        try:
            manifest = load_payload(target)
            if expected_source is not None and manifest["source_sha"] != expected_source:
                raise NativeError("Release source SHA differs from requested source")
            return target
        except NativeError:
            pass
    base = "https://github.com/vbrekher/jacred-bunny/releases/download/" + tag + "/"
    with tempfile.TemporaryDirectory(prefix=".payload-", dir=cache) as temp:
        staging = Path(temp)
        (staging / "manifest.json").write_bytes(fetch_public(base + "manifest.json"))
        try:
            names = payload_spec(json.loads((staging / "manifest.json").read_text()))
        except (ValueError, OSError):
            raise NativeError("Invalid release manifest") from None
        for name in names:
            (staging / name).write_bytes(fetch_public(base + name))
        manifest = load_payload(staging)
        if expected_source is not None and manifest["source_sha"] != expected_source:
            raise NativeError("Release source SHA differs from requested source")
        target.mkdir(mode=0o700, exist_ok=True)
        for path in staging.iterdir():
            os.replace(path, target / path.name)
    return target


class SQL:
    """Bounded libSQL HTTP v2 transport, matching the official Bunny client.

    Each request closes its stream. No transparent replay of writes; SQL errors
    deliberately omit server messages because they can contain row values.
    """
    def __init__(self, url, token, opener=None):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ("libsql", "https") or not parsed.hostname or
            not parsed.hostname.endswith(".bunnydb.net") or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.port not in (None, 443)):
            raise NativeError("Expected a credential-free HTTPS Bunny Database URL")
        self.url = "https://" + parsed.hostname + "/v2/pipeline"
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    @staticmethod
    def encode(value):
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": "1" if value else "0"}
        if isinstance(value, int) and -(2**63) <= value < 2**63:
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float) and math.isfinite(value):
            return {"type": "float", "value": value}
        if isinstance(value, str):
            return {"type": "text", "value": value}
        if isinstance(value, bytes):
            return {"type": "blob", "base64": base64.b64encode(value).decode("ascii")}
        raise NativeError("Unsupported SQL parameter type or range")

    @staticmethod
    def decode(value):
        try:
            kind = value.get("type")
            if kind == "null":
                return None
            if kind == "integer":
                raw = value["value"]
                if not isinstance(raw, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", raw):
                    raise ValueError()
                result = int(raw)
                if not -(2**63) <= result < 2**63:
                    raise ValueError()
                return result
            if kind == "float":
                result = value["value"]
                if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result):
                    raise ValueError()
                return float(result)
            if kind == "text" and isinstance(value["value"], str):
                return value["value"]
            if kind == "blob":
                raw = value["base64"]
                return base64.b64decode(raw + "=" * ((4-len(raw)%4)%4), validate=True)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            pass
        raise NativeError("Invalid SQL response value")

    def exchange(self, requests, baton=None):
        body = json.dumps({"baton": baton, "requests": requests}).encode()
        if len(body) > 4 * 1024 * 1024:
            raise NativeError("SQL request exceeds bounded batch size")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "User-Agent": "jacred-bunny-native-installer"})
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise NativeError("SQL response exceeds bounded result size")
            result = json.loads(raw)
            if not isinstance(result.get("results"), list):
                raise NativeError("Malformed SQL protocol response")
            return result
        except urllib.error.HTTPError as exc:
            raise NativeError("SQL HTTP " + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise NativeError("SQL network failure; do not blindly replay writes") from None
        except (KeyError, ValueError, TypeError, IndexError):
            raise NativeError("Malformed SQL protocol response") from None

    @staticmethod
    def unwrap(payload):
        try:
            result = payload["results"][0]
            if result.get("type") == "error" or result.get("error"):
                raise NativeError("SQL operation rejected; values omitted")
            return result["response"].get("result")
        except (KeyError, TypeError, IndexError):
            raise NativeError("Malformed SQL protocol response") from None

    def send(self, request):
        return self.unwrap(self.exchange([request, {"type": "close"}]))

    def query(self, text, values=()):
        result = self.send({"type": "execute", "stmt": {"sql": text,
            "args": [self.encode(x) for x in values], "named_args": [], "want_rows": True}})
        try:
            columns = [x["name"] for x in result["cols"]]
            if not all(isinstance(name, str) for name in columns) or any(len(row) != len(columns) for row in result["rows"]):
                raise NativeError("Malformed SQL result shape")
            return [dict(zip(columns, (self.decode(x) for x in row))) for row in result["rows"]]
        except (KeyError, TypeError, ValueError):
            raise NativeError("Malformed SQL result shape") from None

    def script(self, text):
        return self.send({"type": "sequence", "sql": text})

    def migrate(self, files):
        self.script("CREATE TABLE IF NOT EXISTS native_schema_migrations (name TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL);")
        applied = {x["name"]: x["sha256"] for x in self.query("SELECT name,sha256 FROM native_schema_migrations")}
        changed = []
        for path in sorted(map(Path, files)):
            if not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+\.sql", path.name):
                raise NativeError("Invalid migration file name")
            content = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != digest:
                    raise NativeError("Applied migration checksum changed; refusing schema drift")
                continue
            # Validate completeness without splitting trigger bodies. Each
            # migration and its checksum commit in the same SQL transaction.
            list(sql_statements(content))
            stamp = datetime.now(timezone.utc).isoformat()
            self.script("BEGIN IMMEDIATE;\n" + content + "\n" +
                "INSERT INTO native_schema_migrations(name,sha256,applied_at) VALUES('" + path.name +
                "','" + digest + "','" + stamp + "');\nCOMMIT;")
            changed.append(path.name)
        return changed


class SQLSession(SQL):
    """Read snapshot across bounded requests; a missing baton aborts the export."""
    def __init__(self, url, token, opener=None):
        super().__init__(url, token, opener)
        self.baton = None

    def send(self, request):
        payload = self.exchange([request], self.baton)
        self.baton = payload.get("baton")
        if payload.get("base_url"):
            self.url = SQL(payload["base_url"], self.token).url
        result = self.unwrap(payload)
        if not isinstance(self.baton, str) or not self.baton:
            raise NativeError("Interactive snapshot session unavailable; backup not accepted")
        return result

    def __enter__(self):
        self.query("BEGIN")
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.query("ROLLBACK" if kind else "COMMIT")
        finally:
            if self.baton:
                try:
                    self.exchange([{"type": "close"}], self.baton)
                finally:
                    self.baton = None


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


@contextmanager
def installation_lock(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise NativeError("Unsafe deployment directory symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / ".install.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NativeError("Another install or resume is running") from None
        yield
    finally:
        os.close(fd)


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
        if self.directory.is_symlink():
            raise NativeError("Unsafe deployment directory symlink")
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

    def create(self, kind, path, body, id_field, get_path, name_field, response_field=None, identity=None):
        """Persist intent before POST; ambiguous creation never causes a replay."""
        old = self.state["resources"].get(kind)
        if old:
            if old.get("status") == "creating":
                raise NativeError("Uncertain %s creation; reconcile account before resume" % kind)
            if identity is not None and old.get("identity") != identity:
                raise NativeError("Saved worker allocation differs; use a reviewed update")
            self.verify(kind)
            return old["id"]
        record = {"name": self.state["name"], "get_path": get_path,
                  "name_field": name_field, "response_field": response_field, "status": "creating",
                  "identity": identity}
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
        identity = record.get("identity")
        if identity:
            templates = remote.get("containerTemplates")
            if (not isinstance(templates, list) or len(templates) != 1 or
                any(templates[0].get(k) != v for k, v in identity["container"].items()) or
                remote.get("autoScaling") != {"min": 1, "max": 1} or
                (remote.get("regionSettings") or {}).get("requiredRegionIds") != [identity["region"]] or
                (remote.get("regionSettings") or {}).get("allowedRegionIds") != [identity["region"]] or
                templates[0].get("volumeMounts") != identity["volumeMounts"] or
                remote.get("volumes") != identity["volumes"]):
                raise NativeError("Remote worker specification changed; refusing mutation")
            if identity.get("environment_sha256") is not None and environment_fingerprint(templates[0].get("environmentVariables")) != identity["environment_sha256"]:
                raise NativeError("Remote private worker configuration changed; refusing mutation")
        return remote

    def sql_client(self, write=False, create=True):
        database = self.verify("database")
        credentials = read_private(self.secret_path)
        field = "database_write_token" if write else "database_read_token"
        if field not in credentials:
            if not create:
                raise NativeError("SQL credentials not configured")
            ident = self.state["resources"]["database"]["id"]
            token = self.api("PUT", "/database/v2/databases/" + str(ident) + "/auth/generate",
                             {"authorization": "full-access" if write else "read-only", "expires_at": None})
            if not isinstance(token, dict) or not isinstance(token.get("token"), str):
                raise NativeError("Missing scoped SQL token")
            credentials[field] = token["token"]
            atomic_private(self.secret_path, credentials)
        return SQL(database["url"], credentials[field])

    @serialized
    def migrate(self, files):
        sql = self.sql_client(write=True)
        changed = sql.migrate(files)
        self.state["migration_state"] = "applied"
        self.state["migration_receipts"] = sql.query("SELECT name,sha256 FROM native_schema_migrations ORDER BY name")
        self.save()
        return changed

    @serialized
    def provision_worker(self, payload):
        # The existing CLI owns image parsing and the legacy payload builder.
        # Persist only non-secret identity fields in the deployment manifest.
        containers = payload.get("containerTemplates")
        if not isinstance(containers, list) or len(containers) != 1:
            raise NativeError("Exactly one bounded worker is required")
        container = containers[0]
        if (container.get("imageNamespace") != "vbrekher" or container.get("imageName") != "jacred-bunny" or
            container.get("imageTag") != self.state["image"].rsplit(":", 1)[-1]):
            raise NativeError("Worker image source differs from verified fork")
        if container.get("imageDigest") != self.state["image_digest"]:
            raise NativeError("Worker image digest differs from verified deployment")
        if payload.get("autoScaling") != {"min": 1, "max": 1}:
            raise NativeError("Staging must have exactly one replica")
        if ((payload.get("regionSettings") or {}).get("requiredRegionIds") != [self.state["region"]] or
            (payload.get("regionSettings") or {}).get("allowedRegionIds") != [self.state["region"]]):
            raise NativeError("Staging worker region mismatch")
        payload = dict(payload, name=self.state["name"])
        identity = {"container": {k: container[k] for k in
                    ("name", "imageNamespace", "imageName", "imageTag", "imageRegistryId", "imageDigest")},
                    "region": self.state["region"], "volumeMounts": container.get("volumeMounts"),
                    "volumes": payload.get("volumes")}
        if self.state.get("catalog_backend") == "bunny-sql":
            identity["environment_sha256"] = environment_fingerprint(container.get("environmentVariables"))
        ident = self.create("worker", "/mc/apps", payload, "id", "/mc/apps/{id}", "name", identity=identity)
        self.state["phase"] = "worker_created_not_deployed"
        self.save()
        return ident

    def verify_worker_for_stop(self):
        try:
            return self.verify("worker")
        except NativeError:
            pending = self.state.get("pending_update")
            if (not pending or not re.fullmatch(r"[0-9a-f]{40}", pending.get("to_source", "")) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", pending.get("to_digest", ""))):
                raise
            # A PATCH response can be lost between a known update intent and
            # journal confirmation. Either saved image may be stopped; every
            # other ownership, mount and allocation check remains mandatory.
            record = self.state["resources"]["worker"]
            original = record["identity"]["container"]
            candidate = dict(original, imageTag="sha-" + pending["to_source"], imageDigest=pending["to_digest"])
            record["identity"]["container"] = candidate
            try:
                return self.verify("worker")
            finally:
                record["identity"]["container"] = original

    @serialized
    def worker_action(self, action):
        if action not in ("start", "stop"):
            raise NativeError("Unknown worker lifecycle action")
        if action == "start" and time.time() >= self.state["stop_at"]:
            raise NativeError("Staging budget deadline expired; renew a bounded window before start")
        if action == "start" and not self.watchdog_available():
            raise NativeError("No live budget watchdog; refusing worker deploy")
        if action == "stop":
            self.verify_worker_for_stop()
        else:
            self.verify("worker")
        if action == "start" and time.time() >= self.state["stop_at"]:
            raise NativeError("Staging deadline expired during verification")
        ident = self.state["resources"]["worker"]["id"]
        verb = "deploy" if action == "start" else "undeploy"
        self.api("POST", "/mc/apps/" + str(ident) + "/" + verb)
        self.state["last_worker_action"] = {"action": action, "requested_at": datetime.now(timezone.utc).isoformat()}
        self.save()

    @serialized
    def renew_window(self, hours):
        if type(hours) not in (int, float) or not 0 < hours <= 24:
            raise NativeError("Staging window must be >0 and <=24 hours")
        self.state["stop_at"] = time.time() + hours * 3600
        self.save()

    @serialized
    def bound_staging_cdn(self):
        for parent, cap in (("edge", 10_000_000), ("worker", 25_000_000)):
            remote = self.verify(parent)
            if parent == "edge":
                links = remote.get("LinkedPullZones") or []
                ids = [x.get("Id") for x in links]
            else:
                ids = [x.get("pullZoneId") for c in remote.get("containerTemplates", [])
                       for x in c.get("endpoints", []) if x.get("type") == "cdn"]
            if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]*", str(ids[0])):
                raise NativeError("Cannot uniquely identify owned linked CDN zone")
            ident = int(ids[0])
            kind = parent + "_cdn"
            previous = self.state["resources"].get(kind)
            if previous and previous["id"] != ident:
                raise NativeError("Linked CDN zone changed; refusing mutation")
            zone = self.api("GET", "/pullzone/" + str(ident))
            if not isinstance(zone, dict) or type(zone.get("Name")) is not str:
                raise NativeError("Missing CDN zone identity")
            if not previous:
                self.state["resources"][kind] = {"id": ident, "name": zone["Name"], "name_field": "Name",
                    "get_path": "/pullzone/{id}", "status": "created", "parent": parent}
                self.save()
            self.verify(kind)
            self.api("POST", "/pullzone/" + str(ident), {"MonthlyBandwidthLimit": cap, "CacheErrorResponses": False})
            confirmed = self.verify(kind)
            if confirmed.get("MonthlyBandwidthLimit") != cap or confirmed.get("CacheErrorResponses") is not False:
                raise NativeError("CDN byte budget not confirmed; worker must not be deployed")
        self.state["cdn_monthly_byte_limits"] = {"edge": 10_000_000, "worker": 25_000_000}
        self.save()

    def watchdog_available(self):
        try:
            heartbeat = read_private(self.directory / "watchdog.json")
            if (heartbeat.get("nonce") != self.state.get("watchdog_nonce") or
                heartbeat.get("status") != "watching" or
                time.time() - heartbeat.get("at", 0) > 90 or
                heartbeat.get("deadline") != self.state["stop_at"]):
                return False
            os.kill(heartbeat["pid"], 0)
            return True
        except (NativeError, OSError, KeyError, TypeError):
            return False

    @serialized
    def start_watchdog(self):
        if self.watchdog_available():
            return
        # A live watcher reads a renewed deadline on its next heartbeat. Let it
        # acknowledge that update instead of racing a second process for its lock.
        try:
            heartbeat = read_private(self.directory / "watchdog.json")
            same = heartbeat.get("nonce") == self.state.get("watchdog_nonce")
            live = same and heartbeat.get("status") == "watching" and time.time() - heartbeat.get("at", 0) < 90
            if live:
                for _ in range(200):
                    if self.watchdog_available():
                        return
                    os.kill(heartbeat["pid"], 0)
                    time.sleep(0.1)
        except (NativeError, OSError, KeyError, TypeError):
            pass
        if time.time() >= self.state["stop_at"]:
            raise NativeError("Cannot arm an expired staging window")
        nonce = secrets.token_hex(16)
        self.state["watchdog_nonce"] = nonce
        self.save()
        log_path = self.directory / "watchdog.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        read_fd, write_fd = os.pipe()
        try:
            actions = [(os.POSIX_SPAWN_DUP2, read_fd, 0), (os.POSIX_SPAWN_DUP2, log_fd, 1),
                       (os.POSIX_SPAWN_DUP2, log_fd, 2)]
            actions += [(os.POSIX_SPAWN_CLOSE, fd) for fd in set((read_fd, write_fd, log_fd)) if fd > 2]
            pid = os.posix_spawn(sys.executable,
                [sys.executable, str(Path(__file__).resolve()), "watch", str(self.directory.resolve())],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUTF8": "1"},
                file_actions=actions, setsid=True)
            os.close(read_fd)
            read_fd = None
            os.write(write_fd, json.dumps({"key": self.api.key, "nonce": nonce}).encode())
            os.close(write_fd)
            write_fd = None
            for _ in range(50):
                if self.watchdog_available():
                    return
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
                time.sleep(0.1)
        except (OSError, BrokenPipeError, NotImplementedError):
            raise NativeError("Cannot launch private staging watchdog") from None
        finally:
            for fd in (log_fd, read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
        raise NativeError("Watchdog did not become ready; worker must not be deployed")

    @serialized
    def record_infrastructure_health(self, observations):
        self.state["last_health"] = observations
        if observations.get("infrastructureReady"):
            self.state["phase"] = "infrastructure_ready_catalog_not_accepted"
        self.save()

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
        if (self.state.get("edge_bundle_sha256") == expected_sha256 and
            self.state.get("edge_release_id") is not None and
            self.state["edge_release_id"] == edge.get("CurrentReleaseId") and
            "database_read_token" in credentials):
            return
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
        current = self.verify("edge")
        self.state["edge_release_id"] = current.get("CurrentReleaseId")
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


def run_watchdog(directory, key, nonce, sleep=time.sleep, clock=time.time):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / ".watchdog.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NativeError("A budget watchdog is already running") from None
        failures = 0
        while True:
            deploy = Deployment(directory, API(key))
            if not deploy.state or deploy.state.get("watchdog_nonce") != nonce:
                raise NativeError("Watchdog ownership token changed")
            heartbeat = {"pid": os.getpid(), "nonce": nonce, "at": clock(),
                         "deadline": deploy.state["stop_at"], "status": "watching"}
            if clock() >= deploy.state["stop_at"]:
                worker = deploy.state["resources"].get("worker")
                if not worker or worker.get("status") != "created":
                    heartbeat["status"] = "expired_no_deployed_worker"
                    atomic_private(directory / "watchdog.json", heartbeat)
                    return
                try:
                    remote = deploy.verify_worker_for_stop()
                    if remote.get("status") == "inactive":
                        heartbeat["status"] = "stopped_confirmed"
                        atomic_private(directory / "watchdog.json", heartbeat)
                        return
                    deploy.worker_action("stop")
                    heartbeat["status"] = "stop_requested"
                except NativeError:
                    failures += 1
                    heartbeat["status"] = "stop_unconfirmed"
                    if failures >= 10:
                        atomic_private(directory / "watchdog.json", heartbeat)
                        raise NativeError("Budget stop unconfirmed; inspect owned worker status") from None
            atomic_private(directory / "watchdog.json", heartbeat)
            sleep(15)
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "watch":
            raise NativeError("Use the jacred-bunny CLI")
        private = json.loads(sys.stdin.read(65536))
        run_watchdog(sys.argv[2], private["key"], private["nonce"])
    except (NativeError, ValueError, KeyError, OSError) as exc:
        print("Watchdog:", str(exc) if isinstance(exc, NativeError) else "private startup failed", file=sys.stderr)
        sys.exit(1)
