using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using JacRed.Models;

namespace JacRed.Infrastructure.Persistence.Native;

/// <summary>Explicit opt-in runtime. A configured SQL failure never falls back to local FileDB.</summary>
internal static class NativeCatalogRuntime
{
    static readonly string Backend = Environment.GetEnvironmentVariable("JACRED_CATALOG_BACKEND") ?? "filedb";
    public static bool Enabled => Backend switch
    {
        "filedb" => false,
        "bunny-sql" => true,
        _ => throw new NativeSqlException("Unknown catalog backend; refusing implicit fallback")
    };
    static readonly Lazy<NativeCatalogStore> Reader = new(() => CreateStore("JACRED_DATABASE_READ_TOKEN"));
    static readonly Lazy<NativeCatalogStore> Writer = new(() => CreateStore("JACRED_DATABASE_WRITE_TOKEN"));
    static NativeCatalogStore CreateStore(string tokenSetting)
    {
        var client = new BunnySqlClient(Required("JACRED_DATABASE_URL"), Required(tokenSetting));
        try
        {
            var store = new NativeCatalogStore(client);
            store.VerifySchema().GetAwaiter().GetResult();
            return store;
        }
        catch { client.Dispose(); throw; }
    }
    public static NativeCatalogStore ReadStore => Reader.Value;
    public static NativeCatalogStore WriteStore => Writer.Value;
    static string Required(string name) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } value ? value : throw new NativeSqlException("Required private SQL setting missing: " + name);

    public static ConcurrentDictionary<string, MasterDbShard> ReadIndex()
    {
        var rows = ReadStore.ReadIndex().GetAwaiter().GetResult();
        return new ConcurrentDictionary<string, MasterDbShard>(rows.Select(row => new KeyValuePair<string, MasterDbShard>(
            (string)row["bucket_key"], new MasterDbShard {
                updateTime = DateTime.Parse((string)row["update_time"], CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind),
                fileTime = row["file_time"] is long time ? time : 0
            })), StringComparer.Ordinal);
    }
}
