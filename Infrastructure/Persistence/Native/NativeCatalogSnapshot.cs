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

    // One read transaction fixes all seven tables to the same SQL snapshot.
    // Transport size bounds fail closed; large catalogs need a paginated read session.
    internal async Task<CatalogSnapshotProof> SnapshotProof(CancellationToken cancellation = default)
    {
        var tables=await sql.Transaction(SnapshotTables.Select(x=>new SqlStatement("SELECT * FROM "+x.Table+" ORDER BY "+x.Order)).ToArray(),write:false,cancellation).ConfigureAwait(false);
        var torrents=tables[2].Rows;
        foreach(var row in torrents)
            if(Hash((string)row["raw_json"])!=(string)row["raw_sha256"])
                throw new NativeSqlException("Snapshot canonical row checksum mismatch; contents omitted");
        var groups=torrents.GroupBy(x=>(string)x["bucket_key"],StringComparer.Ordinal).ToDictionary(x=>x.Key,x=>x.ToArray(),StringComparer.Ordinal);
        foreach(var bucket in tables[0].Rows)
        {
            var rows=groups.GetValueOrDefault((string)bucket["bucket_key"])??Array.Empty<IReadOnlyDictionary<string,object>>();
            var entries=rows.OrderBy(x=>(string)x["source_url"],StringComparer.Ordinal).Select(x=>new[]{(string)x["source_url"],(string)x["raw_sha256"]}).ToArray();
            if((long)bucket["torrent_count"]!=rows.Length || Hash(JsonConvert.SerializeObject(entries,Formatting.None))!=(string)bucket["shard_sha256"])
                throw new NativeSqlException("Snapshot bucket checksum/count mismatch; contents omitted");
        }
        if(groups.Keys.Except(tables[0].Rows.Select(x=>(string)x["bucket_key"]),StringComparer.Ordinal).Any())
            throw new NativeSqlException("Snapshot contains an orphan catalog row");
        using var digest=IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        void Frame(byte type,string value)
        {
            var bytes=value==null?Array.Empty<byte>():Encoding.UTF8.GetBytes(value);
            Span<byte> header=stackalloc byte[9];header[0]=type;BinaryPrimitives.WriteUInt64BigEndian(header[1..],(ulong)bytes.Length);
            digest.AppendData(header);digest.AppendData(bytes);
        }
        Frame((byte)'s',"jacred-sql-relational-snapshot/v1");
        var counts=new Dictionary<string,long>(StringComparer.Ordinal);
        for(int i=0;i<tables.Count;i++)
        {
            var rows=tables[i].Rows;var table=SnapshotTables[i].Table;counts.Add(table,rows.Count);
            Frame((byte)'s',table);Frame((byte)'i',rows.Count.ToString(CultureInfo.InvariantCulture));
            foreach(var row in rows)
            {
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
        }
        return new("jacred-sql-relational-snapshot/v1",Convert.ToHexStringLower(digest.GetHashAndReset()),counts);
    }
}
