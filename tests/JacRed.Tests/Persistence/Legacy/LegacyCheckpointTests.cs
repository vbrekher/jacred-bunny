using System.IO.Compression;
using System.Text;
using JacRed.Infrastructure.Background;
using JacRed.Infrastructure.Persistence;
using Newtonsoft.Json.Linq;
using Xunit;

namespace JacRed.Tests.Persistence.Legacy;

public sealed class LegacyCheckpointTests : IDisposable
{
    readonly string root = Path.Combine(Path.GetTempPath(), "jacred-checkpoint-" + Guid.NewGuid().ToString("N"));
    public LegacyCheckpointTests() => Directory.CreateDirectory(root);

    [Fact]
    public void Verified_readback_preserves_actual_compressed_bytes()
    {
        string path = Path.Combine(root, "master.bz");
        Write(path, "{\"bucket\":{\"updateTime\":\"2026-11-03T00:00:00\",\"fileTime\":133123456789012345}}");
        var before = File.ReadAllBytes(path);
        var expected = JToken.Parse("{\"bucket\":{\"updateTime\":\"2026-11-03T00:00:00\",\"fileTime\":133123456789012345}}");
        FileDB.VerifyAndSyncLegacyJson(path, expected);
        Assert.Equal(before, File.ReadAllBytes(path));
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"count\":1}")]
    [InlineData("{\"count\":2} {\"trailing\":true}")]
    [InlineData("{\"count\":")]
    public void A_swallowed_or_partial_write_cannot_be_attested(string stored)
    {
        string path = Path.Combine(root, "master.bz");
        Write(path, stored);
        Assert.ThrowsAny<Exception>(() => FileDB.VerifyAndSyncLegacyJson(path, JToken.Parse("{\"count\":2}")));
    }

    [Fact]
    public void Corrupt_compressed_master_is_rejected()
    {
        string path = Path.Combine(root, "master.bz");
        File.WriteAllText(path, "not gzip");
        Assert.ThrowsAny<Exception>(() => FileDB.VerifyAndSyncLegacyJson(path, new JObject()));
    }

    [Theory]
    [InlineData("{\"schema\":\"jacred-legacy-shutdown/v1\",\"owner\":\"jacred-bunny\"}", true)]
    [InlineData("{\"schema\":\"other\",\"owner\":\"jacred-bunny\"}", false)]
    [InlineData("{\"schema\":\"jacred-legacy-shutdown/v1\",\"owner\":\"other\"}", false)]
    [InlineData("{}", false)]
    public void Startup_only_invalidates_owned_receipt_namespace(string json, bool owned)
    {
        string path = Path.Combine(root, "receipt.json");
        File.WriteAllText(path, json);
        Assert.Equal(owned, LegacyCheckpointWorker.IsOwnedReceipt(path));
        Assert.Equal(json, File.ReadAllText(path));
    }

    static void Write(string path, string json)
    {
        using var compressed = new GZipStream(File.Create(path), CompressionLevel.Fastest);
        compressed.Write(Encoding.UTF8.GetBytes(json));
    }

    public void Dispose() => Directory.Delete(root, recursive: true);
}
