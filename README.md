<div align="center">

# 🐰 JacRed Bunny

### JacRed on Bunny Magic Containers — no VPS required

**The original .NET 10 torrent search aggregator, adapted for persistent FileDB on bunny.net.**

[![Hosting](https://img.shields.io/badge/Hosting-Bunny_Magic_Containers-ff6600)](https://bunny.net/magic-containers/)
[![Upstream](https://img.shields.io/badge/Upstream-JacRed-1687a7)](https://github.com/jacred-fdb/jacred)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue)](LICENSE)
![Deployment](https://img.shields.io/badge/Release-Deployment_Candidate-yellow)

**[RU · EN · NL Installation Guide](bunny/README.md)** · **[Lampa Setup](bunny/README.md#lampa--sonarr--radarr)** · **[Original Documentation](https://docs.jacred.stream)**

</div>

---

## v0.3.0 — Source and capacity audit

The next development milestone includes a **read-only API/25-tracker inventory**, optional local FileDB size audit, bounded gzip sampling and synthetic SQLite FTS5 probe. It does **not** create Bunny Database resources or migrate any data.

**[Русский · v0.3.0 audit and exact commands](docs/BUNNY_NATIVE_v0.3.0_IMPLEMENTATION.ru.md)**

```bash
python3 -m unittest discover -s tests -p 'test_bunny_native_audit.py' -v
python3 tools/bunny_native_audit.py --strict --synthetic-sqlite 1500 --output "$HOME/Downloads/jacred-bunny-v0.3-source-audit.json"
```

## v0.4.0 — Bunny Native SQL & FileDB importer

The source now includes a **read-only GZip FileDB importer**, atomic SQLite staging,
searchable FTS5/SQL, private SHA256-verified JSONL export, and an explicit
**new-empty-database-only** Bunny SQL uploader. These are staging tools;
no production cutover, live Bunny data acceptance or claim of search parity.

**[Русский — v0.4.0 руководство и команды](docs/BUNNY_NATIVE_v0.4.0_IMPLEMENTATION.ru.md)**

## v0.5.1 — Original key matching, SQLite parity and Bunny acceptance

The preview now searches masterDb aliases with original JacRed name normalization
(including Cyrillic Ё→Е and Щ→Ш), restores FileDB dictionary URL keys, and
adds card/year/season filtering. The complete synthetic GZip FileDB -> real
SQLite -> Edge API pathway is tested automatically.

See [Русский · v0.5.1 implementation and acceptance](docs/BUNNY_NATIVE_v0.5.1_IMPLEMENTATION.ru.md).

The optional read-only Bunny Database acceptance checker and a strict private
original-.NET vs Edge HTTP golden comparator are provided. Real Bunny SQL,
production dataset and complete original-.NET golden parity are NOT CLAIMED.

## v0.5.0 — Edge Scripting API Preview

The Bunny-native path now includes a separate, **opt-in / read-only** Edge Scripting
API preview for Jackett, Torznab, Prowlarr, tracker discovery and SQL search.
It uses the official Bunny Edge SDK + Bunny Database client and requires a
verified completed SQL import manifest before returning search results.

[Русский · v0.5.0 Edge API setup, compatibility matrix and safe staging](docs/BUNNY_NATIVE_v0.5.0_IMPLEMENTATION.ru.md)

**Status:** offline API tests and Edge bundle build PASS. Real Bunny Database access,
original .NET exact search/result parity, real live deployment and production cutover
are **not yet certified**. The existing .NET JacRed remains authoritative.

### Run the isolated source tests

```bash
cd bunny-native/edge
npm install --no-audit --no-fund
npm test
npm run build
```

## Full Bunny-native rewrite roadmap

A documented upgrade path covers complete SQL-backed replacement of FileDB, a TypeScript Edge Scripting API, automatic collection from all 25 supported tracker adapters, shared logical data for multiple replicas, Bunny Storage backup/restore, and optional TorrServer-bunny playback integration.

**[Read the full technical roadmap (Русский)](docs/BUNNY_NATIVE_REWRITE_PLAN.ru.md)**

> This is a development roadmap, **not** a claim that the rewrite or all live integrations are complete. The original .NET process cannot run inside the Edge Scripting isolate; long-running collectors and torrent networking will remain in Bunny Magic Containers.

## What you get

| Feature | Bunny edition |
| :--- | :--- |
| Jackett JSON, Torznab, Native API, Vue interface | ✅ Preserved upstream |
| 25 built-in tracker integrations | ✅ Source retained, each requires real acceptance |
| Local FastDb/FileDB | ✅ Persistent Bunny volume |
| Web configuration editor | ✅ Can write back to persistent `/app/config` |
| Docker image of *this fork's code* | ✅ `Dockerfile.bunny` |
| First-run security | ✅ Distinct API and admin secrets required |
| Cron | ✅ Optional maintenance-only schedule |
| Bunny Database | 🔜 Future optional SQL module; **not** a drop-in FileDB replacement |
| Bunny Edge Scripting | 🔜 Optional edge gateway |
| Automatic cloud backup and verified live deployment | ❌ Not yet implemented or certified |

## Architecture

```text
Samsung TV / Lampa / Sonarr / Radarr / Prowlarr
                        │ HTTPS
                Bunny CDN / Shield
                        │
              Bunny Magic Container
                 JacRed / .NET 10
                  ├─ /app/Data    ← persistent FileDB
                  ├─ /app/config  ← persistent settings
                  └─ maintenance cron (optional)
                        │
             Bunny Storage (planned backups)
             Bunny Database (optional later)
```

## One-command installer (macOS / Linux)

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/vbrekher/jacred-bunny/main/install-bunny.sh \
  -o /tmp/jacred-bunny-install.sh \
  && sh /tmp/jacred-bunny-install.sh
```

The installer prompts for your **Bunny API key**, selects a region, and automatically creates a Magic Container, persistent FileDB/config volumes, HTTPS endpoint, and unique JacRed keys. It checks the published GHCR image before allocating billable resources and asks for `CREATE` before provisioning. An existing GHCR registry is reused; otherwise a GitHub read:packages PAT may be needed.

**[RU / EN / NL step-by-step instructions](bunny/README.md#автоматическая-установка-ru)** · `~/.local/bin/jacred-bunny status` · `credentials` · `start` · `stop`

> The first FileDB is empty until optional official sync or authorized tracker indexing runs. The GitHub Actions image publication must succeed before the first installation. No live Bunny deployment has been claimed.

## Advanced: manual image build

```bash
git clone https://github.com/vbrekher/jacred-bunny.git
cd jacred-bunny
# Sign in to GHCR first; replace YOUR_ACCOUNT:
IMAGE=ghcr.io/YOUR_ACCOUNT/jacred-bunny:0.1.0 bash bunny/build-push.sh
```

Then create a Bunny Magic Containers app, select **one region / one replica**, the published image, HTTP **port 9117**, health check **/health**, and mount **TWO persistent volumes**: `/app/Data` and `/app/config`. Add two different secret environment values: `JACRED_BUNNY_API_KEY` and `JACRED_BUNNY_DEV_KEY`. Do **not** put the admin key in Lampa.

See the [full guide in Russian, English, and Dutch](bunny/README.md) before publicly exposing the instance.

> **Important:** JacRed indexes/searches torrent metadata. It does not download or stream movies itself. A separate compatible playback solution (e.g. TorrServer) is required. A fresh instance starts with an **empty FileDB** until you configure lawful/authorized sync or parsing. External trackers may require credentials, sidecars or proxies, none of which is guaranteed by this hosting profile.

## Credits

A big thank you to **[JacRed maintainers and contributors](https://github.com/jacred-fdb/jacred)** for the original project, API implementations, parsers, FileDB engine and UI. JacRed Bunny is a deployment-oriented derivative, not an official upstream release. The original [AGPL-3.0 license](LICENSE) and attribution remain in place.

**Status:** Source changes pushed as a deployment candidate. .NET build, Magic Containers startup, persistence, cron, authenticated client traffic, and backup/restore require real-world validation before production claims.
