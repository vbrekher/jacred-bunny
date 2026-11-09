# Read-only original-data migration acceptance

This developer harness checks the captured 20-bucket/20-torrent migration candidate. It is not the public installer and does not perform cutover. It starts no service, invokes no collector and sends no account API key.

A private mode-0600 JSON file must contain a credential-free Bunny Database URL in "url" and a separately generated scoped read-only token in "reader". Never commit that file, raw snapshots or private archives. The caller must establish the saved owned database identity before running the harness.

    dotnet build tests/LiveLegacyImport/Read.csproj --configuration Release
    dotnet tests/LiveLegacyImport/bin/Release/net10.0/JacRed.Tests.dll
    dotnet tests/LiveLegacyImport/bin/Release/net10.0/JacRed.Tests.dll --run /private/reader.json

Without arguments it prints a plan and makes no network requests. The explicit run verifies migration receipts, reads all 20 buckets through the actual native DTO/hash/revision code, and computes the complete native relational snapshot checksum. Compare that checksum with an independent Python calculation over the same immutable candidate and a separate remote snapshot.

This tests one real candidate, not arbitrary catalog sizes, every API/client, restored config/Tracks/jobs or production readiness. .NET is a developer acceptance dependency here; the public migration tools must not require local .NET or Docker.
