using System.Text.Json;
using JacRed.Infrastructure.Persistence.Native;
using JacRed.Infrastructure.Persistence.Legacy;
if (args.Length == 0)
{
    Console.WriteLine("PLAN: no network or SQL; use --run with a private reader file");
    return 0;
}
if (args.Length != 2 || args[0] != "--run")
{
    Console.Error.WriteLine("Expected --run and a private JSON file");
    return 2;
}
try
{
    if (OperatingSystem.IsWindows() || new FileInfo(args[1]).LinkTarget != null ||
        (File.GetUnixFileMode(args[1]) & (UnixFileMode.GroupRead | UnixFileMode.GroupWrite |
        UnixFileMode.GroupExecute | UnixFileMode.OtherRead | UnixFileMode.OtherWrite | UnixFileMode.OtherExecute)) != 0)
        throw new Exception("Private regular reader file required");

    using var input = LegacyArchive.OpenSource(args[1]);
    if (input.Length > 65536) throw new Exception("Reader configuration exceeds bound");
    using var document = JsonDocument.Parse(input);
    var credentials = document.RootElement;
    using var sql = new BunnySqlClient(credentials.GetProperty("url").GetString(),
        credentials.GetProperty("reader").GetString());
    var store = new NativeCatalogStore(sql);
    await store.VerifySchema();
    var keys = (await sql.Query(new SqlStatement("SELECT bucket_key FROM buckets ORDER BY bucket_key"))).Rows;
    long records = 0;
    foreach (var key in keys)
    {
        var bucket = await store.ReadBucket((string)key["bucket_key"]);
        if (bucket.Revision != 1 || bucket.RowHashes.Count != bucket.Torrents.Count ||
            bucket.Torrents.Any(pair => pair.Key != pair.Value.url))
            throw new Exception("Native DTO/revision read mismatch");
        records += bucket.Torrents.Count;
    }
    var snapshot = await store.SnapshotProof();
    if (keys.Count != 20 || records != 20 || snapshot.Counts["torrents"] != 20)
        throw new Exception("Original catalog count mismatch");
    Console.WriteLine(JsonSerializer.Serialize(new {
        scope="Actual local .NET native catalog reads against new owned Bunny SQL candidate; not HTTP cutover",
        buckets=keys.Count, torrents=records, all_bucket_dto_reads_verified=true,
        algorithm=snapshot.Algorithm, snapshot_sha256=snapshot.Sha256,
        counts=snapshot.Counts, driver_source_sha=JacRed.VersionInfo.GitFullSha,
        read_only_token_configuration=true, production_ready=false
    }));
    return 0;
}
catch
{
    Console.Error.WriteLine("READ_PROBE_REFUSED: private database details omitted");
    return 1;
}
