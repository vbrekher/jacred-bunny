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
