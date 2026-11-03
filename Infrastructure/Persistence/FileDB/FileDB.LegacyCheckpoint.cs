using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using JacRed.Infrastructure.Persistence.Native;
using Newtonsoft.Json;

namespace JacRed.Infrastructure.Persistence;

public partial class FileDB
{
    static readonly object LegacyWriteGate = new object();
    static bool legacyWritesClosed;

    static void RequireLegacyWritesOpen()
    {
        if (legacyWritesClosed) throw new OperationCanceledException("Legacy catalog is checkpointed for shutdown");
    }

    // Called only after normal HTTP/hosted workers have stopped. A successful
    // old SaveChangesToFile() call is insufficient: it swallows write failures.
    internal static (string MasterSha256, int Keys) VerifiedLegacyShutdownFlush()
    {
        if (NativeCatalogRuntime.Enabled) throw new InvalidOperationException("Legacy checkpoint refuses SQL authority");
        lock (LegacyWriteGate)
        {
            if (openWriteTask.Any(x => Volatile.Read(ref x.Value.openconnection) != 0))
                throw new InvalidOperationException("Legacy write scope still active");
            legacyWritesClosed = true;
        }
        foreach (var entry in openWriteTask.ToArray())
        {
            if (Volatile.Read(ref entry.Value.openconnection) != 0)
                throw new InvalidOperationException("Legacy write scope still active");
            var database = entry.Value.db;
            if (database == null) throw new InvalidOperationException("Legacy write scope unavailable");
            if (!database.savechanges) continue;
            var expected = database.GetSnapshot();
            if (expected.Count == 0 && masterDb.ContainsKey(entry.Key))
                throw new InvalidDataException("Legacy empty bucket still indexed");
            if (expected.Count == 0) continue;
            database.SaveChangesIfNeeded();
            VerifyAndSyncLegacyJson(pathDb(entry.Key), expected);
        }
        var index = masterDb;
        SaveChangesToFile();
        VerifyAndSyncLegacyJson("Data/masterDb.bz", index);
        using var source = File.OpenRead("Data/masterDb.bz");
        return (Convert.ToHexStringLower(SHA256.HashData(source)), index.Count);
    }

    internal static void VerifyAndSyncLegacyJson(string path, object expected)
    {
        // Hash the exact original writer encoding without allocating a second
        // whole index or JSON token tree. Readback must match every byte; the
        // permissive JsonStream.Read error handler cannot establish this.
        byte[] expectedHash;
        using (var hash = SHA256.Create())
        using (var sink = new CryptoStream(Stream.Null, hash, CryptoStreamMode.Write))
        using (var text = new StreamWriter(sink, new UTF8Encoding(false), 8192, leaveOpen: true))
        using (var writer = new JsonTextWriter(text))
        {
            JsonSerializer.Create().Serialize(writer, expected);
            writer.Flush();
            text.Flush();
            sink.FlushFinalBlock();
            expectedHash = hash.Hash;
        }
        using (var file = File.OpenRead(path))
        using (var compressed = new GZipStream(file, CompressionMode.Decompress))
        {
            if (!CryptographicOperations.FixedTimeEquals(expectedHash, SHA256.HashData(compressed)))
                throw new InvalidDataException("Legacy checkpoint read-back mismatch");
        }
        using var durable = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.Read);
        durable.Flush(flushToDisk: true);
    }
}
