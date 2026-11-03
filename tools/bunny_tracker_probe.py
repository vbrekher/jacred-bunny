#!/usr/bin/env python3
"""One HEAD per configured tracker. Reachability is never parser readiness.

No auth, cookies, proxy, CAPTCHA solver, redirects, retries or torrent downloads.
Run from the checkout's tools directory or as a script. Output has no secrets.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from bunny_native_audit import trackers


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configured_hosts(root):
    source = (root / 'Configuration/AppOptions.cs').read_text()
    hosts = {name.lower(): host for name, host in re.findall(
        r'public TrackerSettings\s+(\w+)\s*=\s*new TrackerSettings\("([^"]+)"', source)}
    current = None
    for line in (root / 'Data/init.yaml').read_text().splitlines():
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9]*):', line)
        if match:
            current = match.group(1).lower()
        elif line and not line.startswith(' '):
            current = None
        elif current in hosts and (match := re.fullmatch(r'  host:\s*(https?://[^\s]+)\s*', line)):
            hosts[current] = match.group(1)
    return hosts


def safe_host(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
        parsed.username or parsed.password or parsed.query or parsed.fragment or
        parsed.path not in ('', '/') or parsed.port not in (None, 80, 443)):
        raise ValueError('Unsafe tracker URL')
    for entry in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise ValueError('Non-public tracker address rejected')
    return parsed.scheme + '://' + parsed.netloc + '/'


def classify(code):
    if code == 401:
        return 'NEEDS_AUTH'
    if code in (403, 451):
        return 'BLOCKED_POLICY'
    if code >= 500:
        return 'UPSTREAM_UNAVAILABLE'
    # Even 200 proves no parser, list/detail/magnet or SQL ingestion behavior.
    return 'LIMITED'


def probe(url):
    try:
        url = safe_host(url)
    except (ValueError, OSError):
        return {'status': 'UPSTREAM_UNAVAILABLE', 'http_status': None,
                'observation': 'DNS failed or address rejected; no request sent'}
    req = urllib.request.Request(url, method='HEAD', headers={
        'User-Agent': 'JacRed-Bunny-Readiness/0.5 (+https://github.com/vbrekher/jacred-bunny)'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=8) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        return {'status': 'UPSTREAM_UNAVAILABLE', 'http_status': None,
                'observation': 'HEAD network/TLS timeout or connection failure from this machine'}
    return {'status': classify(code), 'http_status': code,
            'observation': 'One unauthenticated HEAD; redirects not followed; parser and ingestion NOT_TESTED'}


def report(root, live=False):
    hosts = configured_hosts(root)
    rows = trackers(root)
    for row in rows:
        row['host'] = hosts.get(row['id'])
        row['parser_ready'] = False
        row['source_sha256'] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in row['parsers'] + row['sync_services']}
        row.update(probe(row['host']) if live and row['host'] else {
            'status': 'NOT_TESTED', 'http_status': None, 'observation': 'Source inventory only'})
    return {'schema': 'jacred-tracker-reachability/v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'live': live, 'parser_acceptance_pass': False, 'trackers': rows,
            'limitations': 'HEAD reachability from the operator machine is not a Bunny worker parser test, authorized account test, or source-to-SQL acceptance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--live', action='store_true', help='Exactly one unauthenticated HEAD per tracker')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.root, args.live)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    counts = {}
    for row in result['trackers']:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    print(json.dumps({'trackers': len(result['trackers']), 'statuses': counts, 'parser_acceptance_pass': False}))


if __name__ == '__main__':
    main()
