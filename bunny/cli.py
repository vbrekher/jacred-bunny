#!/usr/bin/env python3
"""One-command JacRed Bunny installer; Python standard library only."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.bunny.net"
IMAGE = "ghcr.io/vbrekher/jacred-bunny:main"
APP_NAME = "jacred-bunny"
CONFIG = Path.home() / ".config" / "jacred-bunny" / "config.json"
UA = "JacRed-bunny/0.2 (+https://github.com/vbrekher/jacred-bunny)"

class DeploymentError(Exception):
    pass

def ask(label, default=None, hidden=False):
    suffix = " [" + str(default) + "]" if default is not None else ""
    value = (getpass.getpass(label + suffix + ": ") if hidden else input(label + suffix + ": ")).strip()
    return value or default

def request(method, path, key, payload=None):
    if not path.startswith("/mc/") or "://" in path or ".." in path:
        raise DeploymentError("Unsafe API path")
    headers = {"AccessKey": key, "User-Agent": UA, "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(API + path, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(1024 * 1024)
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        # The API error body may echo secrets: never log it.
        raise DeploymentError("Bunny HTTP " + str(exc.code) + " (" + method + " " + path.split("?")[0] + ")") from None
    except (urllib.error.URLError, TimeoutError):
        raise DeploymentError("Bunny network error (" + method + " " + path.split("?")[0] + ")") from None

def save_config(c):
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG.parent, 0o700)
    temp = CONFIG.with_suffix(".tmp")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(c, out, indent=2)
            out.write("\n")
        os.replace(temp, CONFIG)
        os.chmod(CONFIG, 0o600)
    finally:
        if temp.exists():
            temp.unlink()

def load_config():
    if not CONFIG.is_file():
        raise DeploymentError("Not configured. Run jacred-bunny setup")
    if CONFIG.is_symlink() or CONFIG.stat().st_mode & 0o077:
        raise DeploymentError("Unsafe config; expected regular mode-600 file")
    return json.loads(CONFIG.read_text(encoding="utf-8"))

def image_parts(image):
    m = re.fullmatch(r"ghcr\.io/([a-z0-9][a-z0-9_.-]*)/([a-z0-9][a-z0-9_.-]*):([A-Za-z0-9_][A-Za-z0-9_.-]*)", image)
    if not m:
        raise DeploymentError("Expected GHCR image: ghcr.io/owner/jacred-bunny:tag")
    ns, name, tag = m.groups()
    return {"imageNamespace": ns, "imageName": name, "imageTag": tag, "imagePullPolicy": "always"}

def list_apps(key):
    items, seen, cursor = [], set(), None
    for _ in range(100):
        path = "/mc/apps?limit=100"
        if cursor:
            path += "&nextCursor=" + urllib.parse.quote(cursor, safe="")
        data = request("GET", path, key)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise DeploymentError("Unexpected Bunny apps response; refusing a duplicate-prone create")
        items.extend(data["items"])
        cursor = data.get("cursor")
        if not cursor:
            return items
        if not isinstance(cursor, str) or cursor in seen:
            raise DeploymentError("Invalid apps cursor")
        seen.add(cursor)
    raise DeploymentError("Apps pagination exceeded safe limit")

def same_apps(key):
    return [a for a in list_apps(key) if isinstance(a, dict) and a.get("name") == APP_NAME]

def ensure_no_duplicate(key):
    matches = same_apps(key)
    if matches:
        raise DeploymentError("Existing jacred-bunny app(s): " + ", ".join(str(a.get("id")) for a in matches[:4])
                              + ". Refusing duplicate creation; use doctor/status.")

def regions(data):
    if isinstance(data, dict):
        data = data.get("items") or data.get("regions") or data.get("data")
    if not isinstance(data, list):
        raise DeploymentError("Invalid regions API response")
    out = []
    for a in data:
        if isinstance(a, dict):
            code = a.get("id") or a.get("regionId") or a.get("code")
            name = a.get("name") or a.get("displayName") or str(code)
            if isinstance(code, str) and code:
                out.append((code, name))
    if not out:
        raise DeploymentError("Bunny reports no deployable regions")
    return out

def registry_ids(data):
    if isinstance(data, dict):
        data = data.get("items")
    if not isinstance(data, list):
        raise DeploymentError("Invalid registry list")
    found = []
    for x in data:
        if not isinstance(x, dict):
            continue
        host = str(x.get("hostName") or "").lower().removeprefix("https://").rstrip("/")
        rid = x.get("id")
        if host == "ghcr.io" and type(rid) in (str, int) and re.fullmatch(r"[1-9][0-9]*", str(rid)):
            found.append(str(rid))
    return list(dict.fromkeys(found))

def check_image(key, rid, image):
    f = image_parts(image)
    result = request("POST", "/mc/registries/digest", key, {
        "registryId": str(rid), "imageName": f["imageName"],
        "imageNamespace": f["imageNamespace"], "tag": f["imageTag"]
    })
    value = result.get("digest") if isinstance(result, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value) else None

def ensure_registry(key, image):
    old = registry_ids(request("GET", "/mc/registries", key))
    for rid in old:
        try:
            digest = check_image(key, rid, image)
        except DeploymentError:
            digest = None
        if digest:
            print("Verified GHCR registry:", rid)
            return rid, digest
    if old:
        raise DeploymentError("GHCR registry exists, but cannot access this image/tag. "
                              "Publish the JacRed Bunny package and check GHCR permissions. No app created.")
    print("No GHCR registry is connected. Creating it requires a GitHub read:packages PAT.")
    if ask("Create GHCR registry through Bunny API? (Y/n)", "Y").lower() != "y":
        raise DeploymentError("GHCR registry connection required; no app created.")
    username = ask("GitHub username")
    if not username or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise DeploymentError("Invalid GitHub username")
    pat = ask("GitHub PAT (read:packages)", hidden=True)
    if not pat:
        raise DeploymentError("GitHub PAT required")
    response = request("POST", "/mc/registries", key, {
        "displayName": "JacRed Bunny GHCR", "type": "gitHub",
        "passwordCredentials": {"userName": username, "password": pat}
    })
    if not isinstance(response, dict) or response.get("status") != "saved":
        raise DeploymentError("Registry creation not confirmed: inspect Bunny registry list before retrying")
    current = registry_ids(request("GET", "/mc/registries", key))
    new = [rid for rid in current if rid not in old]
    if len(new) != 1:
        raise DeploymentError("New numeric GHCR registry ID not uniquely verified: stop before app creation")
    digest = check_image(key, new[0], image)
    if not digest:
        raise DeploymentError("GHCR connection created, but image unavailable. No paid app was created.")
    return new[0], digest

def extract_id(data):
    if isinstance(data, dict):
        for name in ("id", "appId", "guid"):
            if isinstance(data.get(name), str) and data[name]:
                return data[name]
    raise DeploymentError("App create response omitted ID. Inspect Bunny account before retrying.")

def build_payload(region, image, registry, api_key, dev_key, data_size):
    if not re.fullmatch(r"[1-9][0-9]*", str(registry)):
        raise DeploymentError("Bunny requires numeric imageRegistryId, not github")
    return {
        "name": APP_NAME, "runtimeType": "shared",
        "autoScaling": {"min": 1, "max": 1},
        "regionSettings": {"allowedRegionIds": [region], "requiredRegionIds": [region]},
        "containerTemplates": [{
            "name": "jacred", **image_parts(image), "imageRegistryId": str(registry),
            "environmentVariables": [
                {"name": "JACRED_BUNNY_API_KEY", "value": api_key},
                {"name": "JACRED_BUNNY_DEV_KEY", "value": dev_key},
                {"name": "JACRED_BUNNY_CRON", "value": "0"}
            ],
            "endpoints": [{"displayName": "jacred-https", "cdn": {
                "isSslEnabled": True,
                "portMappings": [{"containerPort": 9117, "exposedPort": 80}]
            }}],
            "volumeMounts": [
                {"name": "data", "mountPath": "/app/Data"},
                {"name": "config", "mountPath": "/app/config"}
            ]
        }],
        "volumes": [{"name": "data", "size": data_size}, {"name": "config", "size": 1}]
    }

def matching_app(key, ident, region, image, registry):
    data = request("GET", "/mc/apps/" + urllib.parse.quote(ident, safe=""), key)
    if not isinstance(data, dict) or data.get("name") != APP_NAME:
        return False
    c = data.get("containerTemplates")
    if not isinstance(c, list) or len(c) != 1 or not isinstance(c[0], dict):
        return False
    expected = image_parts(image)
    return (c[0].get("name") == "jacred" and
            all(c[0].get(k) == expected[k] for k in ("imageNamespace", "imageName", "imageTag")) and
            str(c[0].get("imageRegistryId")) == str(registry) and
            region in (data.get("regionSettings") or {}).get("requiredRegionIds", []))

def create_app_safely(key, body, region, image, registry):
    try:
        return extract_id(request("POST", "/mc/apps", key, body))
    except DeploymentError as exc:
        print("App response uncertain. Checking Bunny account; NEVER retrying POST blindly.")
        try:
            found = same_apps(key)
            if len(found) == 1 and isinstance(found[0].get("id"), str):
                ident = found[0]["id"]
                if matching_app(key, ident, region, image, registry):
                    print("Recovered exact app:", ident)
                    return ident
        except DeploymentError:
            pass
        raise DeploymentError(str(exc) + ". Creation may already have succeeded. Run doctor before another setup.") from None

def safe_https(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        url = urllib.parse.urlsplit(value if "://" in value else "https://" + value)
        if (url.scheme != "https" or url.username or url.password or
            url.path not in ("", "/") or url.query or url.fragment or url.port not in (None, 443)):
            return None
        host = url.hostname or ""
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            return "https://" + host.lower()
    except ValueError:
        return None

def extract_endpoint(data):
    candidates = []
    def walk(x):
        if isinstance(x, list):
            for item in x:
                walk(item)
        elif isinstance(x, dict):
            selected = x.get("displayEndpoint")
            if isinstance(selected, dict) and str(selected.get("type", "")).lower() == "cdn":
                candidates.append(selected.get("address"))
            if str(x.get("type", "")).lower() == "cdn" and x.get("isSslEnabled") is True:
                candidates.append(x.get("publicHost"))
            cdn = x.get("cdn")
            if isinstance(cdn, dict) and cdn.get("isSslEnabled") is True:
                candidates.extend((cdn.get("publicHost"), cdn.get("hostname"), cdn.get("publicUrl")))
            for field in ("items", "endpoints", "containerTemplates", "containers"):
                if isinstance(x.get(field), (list, dict)):
                    walk(x[field])
    walk(data)
    for candidate in candidates:
        result = safe_https(candidate)
        if result:
            return result
    return None

def fetch_endpoint(key, app_id):
    enc = urllib.parse.quote(app_id, safe="")
    for path in ("/mc/apps/" + enc + "/endpoints", "/mc/apps/" + enc):
        try:
            found = extract_endpoint(request("GET", path, key))
            if found:
                return found
        except DeploymentError:
            pass
    return None

def details(c, show_keys=False):
    print("\n=== JacRed Bunny / Lampa ===")
    url = safe_https(c.get("url"))
    if url:
        print("Web UI:        ", url)
        print("Lampa Jackett: ", url + "/api/v2.0/indexers/all/results")
        print("Sonarr Torznab:", url + "/torznab/api")
    else:
        print("HTTPS: Bunny is still provisioning; run jacred-bunny status")
    print("App ID:        ", c["app_id"])
    print("Region:        ", c["region"])
    print("API key:       ", c["jacred_api_key"] if show_keys else "[hidden; use jacred-bunny credentials]")
    print("Admin key:     ", c["jacred_dev_key"] if show_keys else "[hidden; admin only]")
    print("Note: First-run FileDB is empty until authorized sync/parsing is configured.")

def setup(args):
    if CONFIG.exists():
        raise DeploymentError("Local installation already configured: use status/start/stop/credentials.")
    print("\nJacRed Bunny — automatic Magic Containers installation")
    print("No Docker/.NET required on this computer. Bunny account key stays in local mode-600 config.")
    key = ask("Bunny account API key", hidden=True)
    if not key:
        raise DeploymentError("Bunny account API key required")
    ensure_no_duplicate(key)
    print("Verifying GHCR image before creating paid resources...")
    registry, digest = ensure_registry(key, args.image)
    print("Image verified:", args.image, digest[:27] + "...")
    available = regions(request("GET", "/mc/regions", key))
    for i, (code, name) in enumerate(available, 1):
        print(" ", i, code, name)
    default = next((code for code, _ in available if code.upper() == "AMS"), available[0][0])
    selection = ask("Region code/number", default)
    region = (available[int(selection)-1][0] if str(selection).isdigit() and
              1 <= int(selection) <= len(available) else
              next((code for code, _ in available if code.lower() == str(selection).lower()), None))
    if not region:
        raise DeploymentError("Invalid region selection")
    if not 1 <= args.data_gb <= 1000:
        raise DeploymentError("Data volume size must be 1..1000 GB")
    api_secret, dev_secret = secrets.token_urlsafe(42), secrets.token_urlsafe(42)
    body = build_payload(region, args.image, registry, api_secret, dev_secret, args.data_gb)
    print("\nPlan: 1 Magic Container, 1 region, HTTPS, 2 persistent volumes.")
    print("Region:", region, "| FileDB:", args.data_gb, "GB | config: 1 GB")
    print("Storage allocation approximately $%.2f/month, PLUS compute and traffic." % ((args.data_gb + 1) * 0.10))
    print("No optional Bunny Database, Scripting, or paid Anycast.")
    enable_sync = ask("Enable official JacRed remote sync on first boot? (y/N)", "N").lower() == "y"
    if enable_sync:
        body["containerTemplates"][0]["environmentVariables"].append(
            {"name": "JACRED_BUNNY_SYNC_API", "value": "https://sync.jacred.stream"})
        print("Official sync is optional; initial indexing may take considerable time and bandwidth.")
    if ask("Type CREATE to confirm paid resources") != "CREATE":
        print("Cancelled. No JacRed Bunny app created.")
        return
    ensure_no_duplicate(key)
    ident = create_app_safely(key, body, region, args.image, registry)
    config = {"api_key": key, "app_id": ident, "region": region, "image": args.image,
              "image_digest_at_create": digest, "data_gb": args.data_gb,
              "jacred_api_key": api_secret, "jacred_dev_key": dev_secret, "url": None}
    save_config(config)  # Save ID BEFORE deploy, for recovery after failure.
    print("App created:", ident, "; credentials saved locally.")
    if not args.no_deploy:
        try:
            request("POST", "/mc/apps/" + urllib.parse.quote(ident, safe="") + "/deploy", key)
            print("Deploy requested; provisioning is asynchronous.")
        except DeploymentError as exc:
            print("Deploy failed; app ID is saved. Run jacred-bunny start:", exc, file=sys.stderr)
    config["url"] = fetch_endpoint(key, ident)
    save_config(config)
    details(config, show_keys=True)
    if config.get("url"):
        probe(config["url"])

def probe(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=8) as response:
            print("Live health HTTP:", response.status)
    except Exception:
        print("Live health: not ready yet; check jacred-bunny status")

def status(show_keys=False):
    c = load_config()
    app = request("GET", "/mc/apps/" + urllib.parse.quote(c["app_id"], safe=""), c["api_key"])
    print("Bunny status:", app.get("status", "(unknown)") if isinstance(app, dict) else "(unknown)")
    current = fetch_endpoint(c["api_key"], c["app_id"])
    if current and current != c.get("url"):
        c["url"] = current
        save_config(c)
    details(c, show_keys)
    if current:
        probe(current)

def doctor():
    key = load_config()["api_key"] if CONFIG.exists() else ask("Bunny account key (read-only)", hidden=True)
    apps = same_apps(key)
    print("JacRed Bunny apps found:", len(apps))
    for item in apps:
        print(" ", item.get("id"), "status:", item.get("status"))
    print("GHCR numeric registry IDs:", ", ".join(registry_ids(request("GET", "/mc/registries", key))) or "(none)")
    try:
        limits = request("GET", "/mc/limits", key)
        for k in ("existingNumberOfApplications", "maxNumberOfApplications", "maxNumberOfVolumesPerApplication"):
            if isinstance(limits, dict) and k in limits:
                print(k, limits[k])
    except DeploymentError:
        print("Account limits API not available")

def lifecycle(action):
    c = load_config()
    verb = "deploy" if action == "start" else "undeploy"
    request("POST", "/mc/apps/" + urllib.parse.quote(c["app_id"], safe="") + "/" + verb, c["api_key"])
    print(verb.upper(), "requested:", c["app_id"])
    print("Note: Persistent storage may remain billable while stopped.")

def self_test():
    assert image_parts(IMAGE)["imageTag"] == "main"
    assert registry_ids({"items": [
        {"hostName": "https://ghcr.io", "id": 11126},
        {"hostName": "ghcr.io", "id": "11126"},
        {"hostName": "ghcr.io", "id": "github"},
        {"hostName": "wrong-ghcr.io", "id": 42}
    ]}) == ["11126"]
    p = build_payload("AMS", IMAGE, "11126", "a"*42, "b"*42, 10)
    assert p["autoScaling"] == {"min": 1, "max": 1}
    assert len(p["volumes"]) == 2 and len(p["containerTemplates"][0]["volumeMounts"]) == 2
    assert p["containerTemplates"][0]["endpoints"][0]["cdn"]["portMappings"][0]["containerPort"] == 9117
    assert safe_https("mc-foo.bunny.run") == "https://mc-foo.bunny.run"
    assert safe_https("http://127.0.0.1:9117") is None
    assert safe_https("https://mc-foo.bunny.run/private") is None
    assert extract_endpoint({"items": [{"type": "anycast", "publicHost": "1.2.3.4"},
                                       {"type": "cdn", "isSslEnabled": True, "publicHost": "mc-foo.bunny.run"}]}) == "https://mc-foo.bunny.run"
    print("JACRED_BUNNY_CLI_SELF_TEST_PASS")

def main():
    parser = argparse.ArgumentParser(description="JacRed Bunny: Magic Containers installer")
    commands = parser.add_subparsers(dest="action")
    s = commands.add_parser("setup")
    s.add_argument("--image", default=os.environ.get("JACRED_BUNNY_IMAGE", IMAGE))
    s.add_argument("--data-gb", type=int, default=10)
    s.add_argument("--no-deploy", action="store_true")
    for name in ("status", "credentials", "doctor", "start", "stop", "self-test"):
        commands.add_parser(name)
    args = parser.parse_args()
    try:
        if args.action is None:
            setup(argparse.Namespace(image=os.environ.get("JACRED_BUNNY_IMAGE", IMAGE), data_gb=10, no_deploy=False))
        elif args.action == "setup":
            setup(args)
        elif args.action == "status": status()
        elif args.action == "credentials": status(show_keys=True)
        elif args.action == "doctor": doctor()
        elif args.action in ("start", "stop"): lifecycle(args.action)
        elif args.action == "self-test": self_test()
    except (DeploymentError, EOFError, KeyboardInterrupt) as exc:
        print("\nERROR:", str(exc) or "Interrupted", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
