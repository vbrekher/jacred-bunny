using System.Text.Json;
using System.Text.RegularExpressions;
using JacRed.Infrastructure.Persistence.Native;

string url=Environment.GetEnvironmentVariable("JACRED_DATABASE_URL")??throw new Exception("Missing SQL URL");
string token=Environment.GetEnvironmentVariable("JACRED_DATABASE_READ_TOKEN")??throw new Exception("Missing scoped SQL reader");
if(!Regex.IsMatch(token,@"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"))throw new Exception("Only scoped SQL JWTs are accepted; never provide an account API key");
using var deadline=new CancellationTokenSource(TimeSpan.FromMinutes(3));var ct=deadline.Token;
using var client=new BunnySqlClient(url,token);var store=new NativeCatalogStore(client);
await store.VerifySchema(ct);
var started=DateTime.UtcNow;
var before=await store.SnapshotProof(ct);
var index=await store.ReadIndex(ct);
var expected=(await client.Query(new SqlStatement("SELECT bucket_key,source_url,raw_sha256 FROM torrents ORDER BY bucket_key,source_url"),ct)).Rows;
long seen=0;
foreach(var entry in index)
{
    var key=(string)entry["bucket_key"];var bucket=await store.ReadBucket(key,ct);
    var target=expected.Where(x=>(string)x["bucket_key"]==key).ToDictionary(x=>(string)x["source_url"],x=>(string)x["raw_sha256"],StringComparer.Ordinal);
    if(bucket.Torrents.Count!=target.Count || bucket.RowHashes.Count!=target.Count || target.Any(x=>!bucket.RowHashes.TryGetValue(x.Key,out var actual)||actual!=x.Value))throw new Exception("Native bucket read differs from canonical SQL rows");
    seen+=bucket.Torrents.Count;
}
if(seen!=expected.Count||seen!=before.Counts["torrents"]||index.Count!=before.Counts["buckets"])throw new Exception("Native read totals differ from service snapshot");
var after=await store.SnapshotProof(ct);
if(before.Sha256!=after.Sha256)throw new Exception("Catalog changed during read slice; result not accepted");
Console.WriteLine(JsonSerializer.Serialize(new { source_sha=JacRed.VersionInfo.GitFullSha,scope="Real Bunny SQL read-only native bucket/index and snapshot slice; not large live catalog or deployed worker acceptance",bucket_count=index.Count,torrent_count=seen,all_native_row_hashes_match_sql=true,canonical_relational_sha256=before.Sha256,canonical_unchanged=true,elapsed_seconds=(DateTime.UtcNow-started).TotalSeconds,production_acceptance=false }));
