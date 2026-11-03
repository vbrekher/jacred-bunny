using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using JacRed.Models.Details;
using Newtonsoft.Json;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed record CatalogWrite(CatalogBucket Snapshot, IReadOnlyDictionary<string, TorrentDetails> Torrents);
internal sealed record CatalogBucket(string Key, long Revision, IReadOnlyDictionary<string, TorrentDetails> Torrents,
    IReadOnlyDictionary<string, string> RowHashes);

/// <summary>Canonical SQL rows, atomic optimistic writes, durable commit receipts. No filesystem fallback.</summary>
internal sealed partial class NativeCatalogStore
{
    readonly BunnySqlClient sql;
    static readonly JsonSerializerSettings JsonSettings = new()
    {
        TypeNameHandling = TypeNameHandling.None,
        MaxDepth = 128,
        DateTimeZoneHandling = DateTimeZoneHandling.Utc
    };

    public NativeCatalogStore(BunnySqlClient sql) => this.sql = sql;
    static string Hash(string text) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(text)));

    internal async Task VerifySchema(CancellationToken cancellation = default)
    {
        var names = new[] { "0001_catalog.sql", "0002_search_fts5.sql", "0004_catalog_commits.sql" };
        var receipts = (await sql.Query(new SqlStatement("SELECT name,sha256 FROM native_schema_migrations ORDER BY name"), cancellation).ConfigureAwait(false)).Rows;
        if (receipts.Count != names.Length) throw new NativeSqlException("Native catalog schema version not supported by this build");
        foreach (var name in names)
        {
            using var source = typeof(NativeCatalogStore).Assembly.GetManifestResourceStream("JacRed.Native.Migrations." + name);
            if (source == null || !receipts.Any(row => (string)row["name"] == name &&
                (string)row["sha256"] == Convert.ToHexStringLower(SHA256.HashData(source))))
                throw new NativeSqlException("Native catalog migration checksum differs from this build");
        }
    }

    public async Task<CatalogBucket> ReadBucket(string key, CancellationToken cancellation = default)
    {
        ValidateKey(key);
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromMinutes(2));cancellation=deadline.Token;
        await using var session=await sql.OpenReadSession(cancellation).ConfigureAwait(false);
        var version=(await session.Query(new SqlStatement("SELECT COALESCE((SELECT revision FROM catalog_bucket_versions WHERE bucket_key=?),0) AS revision,(SELECT torrent_count FROM buckets WHERE bucket_key=?) AS expected_count",key,key),cancellation).ConfigureAwait(false)).Rows[0];
        var rows = new Dictionary<string, TorrentDetails>(StringComparer.Ordinal);
        var hashes = new Dictionary<string, string>(StringComparer.Ordinal);
        await foreach (var row in SnapshotRows(session,"torrents","bucket_key,source_url",cancellation,bucketKey:key,projection:new[]{"source_url","raw_json","raw_sha256"}).ConfigureAwait(false))
        {
            var source = (string)row["source_url"];
            var json = (string)row["raw_json"];
            if (Hash(json) != (string)row["raw_sha256"])
                throw new NativeSqlException("Canonical torrent checksum mismatch");
            TorrentDetails torrent;
            try { torrent = JsonConvert.DeserializeObject<TorrentDetails>(json, JsonSettings); }
            catch (JsonException) { throw new NativeSqlException("Canonical torrent payload invalid; content omitted"); }
            if (torrent == null || torrent.url != source)
                throw new NativeSqlException("Canonical source URL mismatch");
            rows.Add(source, torrent);hashes.Add(source, Hash(json));
        }
        if(version["expected_count"] is long expected ? expected!=rows.Count : rows.Count!=0)
            throw new NativeSqlException("Catalog bucket read count mismatch; partial result refused");
        return new CatalogBucket(key, (long)version["revision"], rows, hashes);
    }

    sealed record WritePlan(string Id, string Key, string Digest, long Revision, IReadOnlyList<SqlStatement> Statements);

    public async Task<long> WriteBucket(CatalogBucket snapshot, IReadOnlyDictionary<string, TorrentDetails> torrents,
        string commitId, CancellationToken cancellation = default)
        => (await WriteBuckets(new[] { new CatalogWrite(snapshot, torrents) }, commitId, cancellation).ConfigureAwait(false))[0];

    public async Task<IReadOnlyList<long>> WriteBuckets(IReadOnlyList<CatalogWrite> writes, string commitId, CancellationToken cancellation = default)
    {
        if (!Guid.TryParseExact(commitId, "N", out _) || writes.Count == 0 || writes.Count > 32 ||
            writes.Select(x => x.Snapshot.Key).Distinct(StringComparer.Ordinal).Count() != writes.Count)
            throw new NativeSqlException("Unique bucket scopes and a durable commit ID are required");
        var group = Hash(JsonConvert.SerializeObject(writes.Select(x => x.Snapshot.Key).OrderBy(x => x, StringComparer.Ordinal)));
        var plans = writes.Select(x => PlanWrite(x.Snapshot, x.Torrents,
            writes.Count == 1 ? commitId : Hash(commitId + group + x.Snapshot.Key)[..32])).ToArray();
        var ids = plans.Select(x => (object)x.Id).ToArray();
        var existing = await sql.Query(new SqlStatement("SELECT commit_id,bucket_key,content_sha256,revision FROM catalog_commits WHERE commit_id IN (" + string.Join(',', ids.Select(_ => "?")) + ")", ids), cancellation).ConfigureAwait(false);
        if (existing.Rows.Count != 0)
        {
            if (existing.Rows.Count != plans.Length) throw new NativeSqlException("Incomplete grouped commit receipt; refusing replay");
            var receipts = existing.Rows.ToDictionary(x => (string)x["commit_id"], StringComparer.Ordinal);
            foreach (var plan in plans)
                if (!receipts.TryGetValue(plan.Id, out var receipt) || (string)receipt["bucket_key"] != plan.Key || (string)receipt["content_sha256"] != plan.Digest)
                    throw new NativeSqlException("Commit ID already used for different content");
            return plans.Select(x => (long)receipts[x.Id]["revision"]).ToArray();
        }
        // One server transaction covers every revision guard, source removal and destination update.
        await sql.PagedWrite(plans.SelectMany(x => x.Statements).ToArray(), cancellation).ConfigureAwait(false);
        return plans.Select(x => x.Revision).ToArray();
    }

    static WritePlan PlanWrite(CatalogBucket snapshot, IReadOnlyDictionary<string, TorrentDetails> torrents, string commitId)
    {
        ValidateKey(snapshot.Key);
        if (!Guid.TryParseExact(commitId, "N", out _)) throw new NativeSqlException("A durable commit ID is required");
        if (snapshot.Revision < 0 || snapshot.Revision == long.MaxValue)
            throw new NativeSqlException("Bucket revision or bounded write size invalid");
        var encoded = new SortedDictionary<string, (TorrentDetails Torrent, string Json, string Hash)>(StringComparer.Ordinal);
        foreach (var pair in torrents)
        {
            if (pair.Value == null || string.IsNullOrWhiteSpace(pair.Key) || pair.Key != pair.Value.url)
                throw new NativeSqlException("Torrent source URL/key mismatch");
            string json;
            try { json = JsonConvert.SerializeObject(pair.Value, JsonSettings); }
            catch (JsonException) { throw new NativeSqlException("Torrent cannot be serialized; private content omitted"); }
            encoded.Add(pair.Key, (pair.Value, json, Hash(json)));
        }
        var digest = Hash(JsonConvert.SerializeObject(encoded.Select(x => new[] { x.Key, x.Value.Hash }).ToArray(), Formatting.None));
        long revision = snapshot.Revision + 1;
        var statements = new List<SqlStatement> {
            new("INSERT INTO catalog_write_guards(commit_id,valid) VALUES(?,CASE WHEN COALESCE((SELECT revision FROM catalog_bucket_versions WHERE bucket_key=?),0)=? THEN 1 ELSE 0 END)", commitId,snapshot.Key,snapshot.Revision),
            new("INSERT INTO catalog_bucket_versions(bucket_key,revision) VALUES(?,?) ON CONFLICT(bucket_key) DO UPDATE SET revision=excluded.revision", snapshot.Key,revision)
        };
        var updated = encoded.Values.Select(x => x.Torrent.updateTime).DefaultIfEmpty(DateTime.UtcNow).Max();
        string stamp = updated.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);
        statements.Add(new SqlStatement("INSERT INTO buckets(bucket_key,key_md5,update_time,file_time,shard_sha256,torrent_count) VALUES(?,?,?,?,?,?) ON CONFLICT(bucket_key) DO UPDATE SET update_time=excluded.update_time,file_time=excluded.file_time,shard_sha256=excluded.shard_sha256,torrent_count=excluded.torrent_count",
            snapshot.Key, Convert.ToHexStringLower(MD5.HashData(Encoding.UTF8.GetBytes(snapshot.Key))), stamp, updated.Year >= 1601 ? updated.ToFileTimeUtc() : 0L, digest, torrents.Count));
        foreach (var removed in snapshot.RowHashes.Keys.Except(encoded.Keys, StringComparer.Ordinal))
            statements.Add(new SqlStatement("DELETE FROM torrents WHERE bucket_key=? AND source_url=?",snapshot.Key,removed));
        foreach (var pair in encoded)
        {
            var value = pair.Value;
            if (snapshot.RowHashes.TryGetValue(pair.Key, out var previous) && previous == value.Hash) continue;
            statements.Add(new SqlStatement("INSERT INTO torrents(bucket_key,source_url,tracker_name,title,name,original_name,magnet,update_time,raw_json,raw_sha256) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bucket_key,source_url) DO UPDATE SET tracker_name=excluded.tracker_name,title=excluded.title,name=excluded.name,original_name=excluded.original_name,magnet=excluded.magnet,update_time=excluded.update_time,raw_json=excluded.raw_json,raw_sha256=excluded.raw_sha256",
                snapshot.Key,pair.Key,value.Torrent.trackerName,value.Torrent.title,value.Torrent.name,value.Torrent.originalname,
                value.Torrent.magnet,value.Torrent.updateTime.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture),value.Json,value.Hash));
        }
        statements.Add(new SqlStatement("DELETE FROM bucket_aliases WHERE bucket_key=?",snapshot.Key));
        foreach (var alias in snapshot.Key.Split(':').Where(x => !string.IsNullOrWhiteSpace(x)).Distinct(StringComparer.Ordinal))
            statements.Add(new SqlStatement("INSERT INTO bucket_aliases(alias,bucket_key) VALUES(?,?)",alias,snapshot.Key));
        if (torrents.Count == 0)
        {
            statements.Add(new SqlStatement("DELETE FROM bucket_aliases WHERE bucket_key=?",snapshot.Key));
            statements.Add(new SqlStatement("DELETE FROM buckets WHERE bucket_key=?",snapshot.Key));
        }
        statements.Add(new SqlStatement("INSERT INTO catalog_commits(commit_id,bucket_key,content_sha256,revision) VALUES(?,?,?,?)",commitId,snapshot.Key,digest,revision));
        statements.Add(new SqlStatement("DELETE FROM catalog_write_guards WHERE commit_id=?",commitId));
        return new WritePlan(commitId,snapshot.Key,digest,revision,statements);
    }

    internal async Task<SqlResult> IndexQuery(CancellationToken cancellation = default)
    {
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromMinutes(2));cancellation=deadline.Token;
        await using var session=await sql.OpenReadSession(cancellation).ConfigureAwait(false);
        long expected=(long)(await session.Query(new SqlStatement("SELECT count(*) AS n FROM buckets"),cancellation).ConfigureAwait(false)).Rows[0]["n"];
        var rows=new List<IReadOnlyDictionary<string,object>>();
        await foreach(var row in SnapshotRows(session,"buckets","bucket_key",cancellation,projection:new[]{"bucket_key","update_time","file_time"},pageRows:512).ConfigureAwait(false))
            rows.Add(row);
        if(rows.Count!=expected)throw new NativeSqlException("Catalog index read count mismatch; partial result refused");
        return new SqlResult(rows,0);
    }

    public async Task<IReadOnlyList<IReadOnlyDictionary<string, object>>> ReadIndex(CancellationToken cancellation = default)
        => (await IndexQuery(cancellation).ConfigureAwait(false)).Rows;

    internal static CatalogBucket CommittedSnapshot(string key, long revision, IReadOnlyDictionary<string, TorrentDetails> rows)
        => new(key, revision, new Dictionary<string, TorrentDetails>(rows, StringComparer.Ordinal),
            rows.ToDictionary(pair => pair.Key, pair => Hash(JsonConvert.SerializeObject(pair.Value, JsonSettings)), StringComparer.Ordinal));

    static void ValidateKey(string key)
    {
        if (key == null || key.Length > 2048 || key.Any(char.IsControl)) throw new NativeSqlException("Invalid catalog bucket key");
    }
}
