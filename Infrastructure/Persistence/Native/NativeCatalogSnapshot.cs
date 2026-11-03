using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed record CatalogSnapshotProof(string Algorithm, string Sha256, IReadOnlyDictionary<string,long> Counts);

internal sealed partial class NativeCatalogStore
{
    internal static readonly (string Table,string Order)[] SnapshotTables = {
        ("buckets","bucket_key"), ("bucket_aliases","alias,bucket_key"), ("torrents","bucket_key,source_url"),
        ("track_file_inventory","relative_path"), ("native_imports","id"),
        ("catalog_bucket_versions","bucket_key"), ("catalog_commits","commit_id")
    };

    // Every page uses the same read transaction. No entire-catalog row buffer.
    internal async Task<CatalogSnapshotProof> SnapshotProof(CancellationToken cancellation = default)
    {
        await using var session=await sql.OpenReadSession(cancellation).ConfigureAwait(false);
        var orphans=await session.Query(new SqlStatement("SELECT count(*) AS n FROM torrents t LEFT JOIN buckets b ON b.bucket_key=t.bucket_key WHERE b.bucket_key IS NULL"),cancellation).ConfigureAwait(false);
        if((long)orphans.Rows[0]["n"]!=0)throw new NativeSqlException("Snapshot contains an orphan catalog row");
        using var digest=IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        void Frame(byte type,string value)
        {
            var bytes=value==null?Array.Empty<byte>():Encoding.UTF8.GetBytes(value);
            Span<byte> header=stackalloc byte[9];header[0]=type;BinaryPrimitives.WriteUInt64BigEndian(header[1..],(ulong)bytes.Length);
            digest.AppendData(header);digest.AppendData(bytes);
        }
        Frame((byte)'s',"jacred-sql-relational-snapshot/v1");
        var counts=new Dictionary<string,long>(StringComparer.Ordinal);
        foreach(var spec in SnapshotTables)
        {
            var table=spec.Table;
            var count=(long)(await session.Query(new SqlStatement("SELECT count(*) AS n FROM "+table),cancellation).ConfigureAwait(false)).Rows[0]["n"];
            if(count<0)throw new NativeSqlException("Snapshot count invalid");
            counts.Add(table,count);
            Frame((byte)'s',table);Frame((byte)'i',count.ToString(CultureInfo.InvariantCulture));
            long seen=0;
            await foreach(var row in SnapshotRows(session,table,spec.Order,cancellation).ConfigureAwait(false))
            {
                if(++seen>count)throw new NativeSqlException("Snapshot page count exceeded transaction count");
                if(table=="torrents" && Hash((string)row["raw_json"])!=(string)row["raw_sha256"])
                    throw new NativeSqlException("Snapshot canonical row checksum mismatch; contents omitted");
                if(table=="buckets")
                {
                    // Per-bucket receipt semantics remain the original .NET
                    // UTF-16 ordinal URL ordering, not SQLite UTF-8 ordering.
                    var entries=(await session.Query(new SqlStatement("SELECT source_url,raw_sha256 FROM torrents WHERE bucket_key=?",row["bucket_key"]),cancellation).ConfigureAwait(false)).Rows;
                    var pairs=entries.OrderBy(x=>(string)x["source_url"],StringComparer.Ordinal).Select(x=>new[]{(string)x["source_url"],(string)x["raw_sha256"]}).ToArray();
                    if((long)row["torrent_count"]!=entries.Count || Hash(JsonConvert.SerializeObject(pairs,Formatting.None))!=(string)row["shard_sha256"])
                        throw new NativeSqlException("Snapshot bucket checksum/count mismatch; contents omitted");
                }
                Frame((byte)'i',row.Count.ToString(CultureInfo.InvariantCulture));
                foreach(var column in row.OrderBy(x=>x.Key,StringComparer.Ordinal))
                {
                    Frame((byte)'s',column.Key);
                    switch(column.Value)
                    {
                        case null:Frame((byte)'n',null);break;
                        case string text:Frame((byte)'s',text);break;
                        case long integer:Frame((byte)'i',integer.ToString(CultureInfo.InvariantCulture));break;
                        default:throw new NativeSqlException("Unsupported snapshot scalar type; no checksum accepted");
                    }
                }
            }
            if(seen!=count)throw new NativeSqlException("Snapshot page count incomplete");
        }
        return new("jacred-sql-relational-snapshot/v1",Convert.ToHexStringLower(digest.GetHashAndReset()),counts);
    }
    internal static async System.Collections.Generic.IAsyncEnumerable<IReadOnlyDictionary<string,object>> SnapshotRows(
        BunnySqlClient.SqlSession session,string table,string order,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellation=default)
    {
        // Identifiers originate from the fixed schema list, never HTTP input.
        if(!SnapshotTables.Any(x=>x.Table==table&&x.Order==order))throw new NativeSqlException("Unknown snapshot table");
        var metadata=(await session.Query(new SqlStatement("PRAGMA table_info("+table+")"),cancellation).ConfigureAwait(false)).Rows;
        var columns=metadata.Select(x=>(string)x["name"]).ToArray();
        if(columns.Length==0||columns.Length>64||columns.Any(x=>!System.Text.RegularExpressions.Regex.IsMatch(x,"^[a-z][a-z0-9_]*$")))
            throw new NativeSqlException("Unsupported snapshot schema");
        var keys=order.Split(',');
        var weight="1024+6*("+string.Join("+",columns.Select(x=>"coalesce(length(CAST("+x+" AS BLOB)),0)"))+")";
        object[] position=null;
        while(true)
        {
            cancellation.ThrowIfCancellationRequested();
            var where=position==null?"":" WHERE ("+order+") > ("+string.Join(",",keys.Select(_=>"?"))+")";
            var statement="WITH selected AS (SELECT * FROM "+table+where+" ORDER BY "+order+" LIMIT 64), weighted AS (SELECT *,row_number() OVER (ORDER BY "+order+") AS __row,sum("+weight+") OVER (ORDER BY "+order+" ROWS UNBOUNDED PRECEDING) AS __bytes FROM selected) SELECT * FROM weighted WHERE __row=1 OR __bytes<=1048576 ORDER BY "+order;
            var page=(await session.Query(new SqlStatement(statement,position??Array.Empty<object>()),cancellation).ConfigureAwait(false)).Rows;
            if(page.Count==0)yield break;
            if(page.Count>64)throw new NativeSqlException("Snapshot page exceeds row bound");
            foreach(var row in page)
            {
                if(columns.Any(x=>!row.ContainsKey(x))||keys.Any(x=>!row.TryGetValue(x,out var value)||value is not (string or long)))
                    throw new NativeSqlException("Snapshot page shape invalid");
                yield return columns.ToDictionary(x=>x,x=>row[x],StringComparer.Ordinal);
            }
            var next=keys.Select(x=>page[^1][x]).ToArray();
            if(position!=null&&position.SequenceEqual(next))throw new NativeSqlException("Snapshot cursor did not advance");
            position=next;
        }
    }

}
