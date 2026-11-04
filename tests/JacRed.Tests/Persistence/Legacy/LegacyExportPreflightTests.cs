using System.Security.Cryptography;
using System.Text.Json;
using JacRed.Infrastructure.Persistence.Legacy;
using Xunit;

namespace JacRed.Tests.Persistence.Legacy;

public sealed class LegacyExportPreflightTests : IDisposable
{
    readonly string root = Path.Combine(Directory.GetCurrentDirectory(), ".legacy-preflight-" + Guid.NewGuid().ToString("N"));
    string Data => Path.Combine(root, "Data");
    string Config => Path.Combine(root, "config");
    const string Key = "dummy_administrative_key_1234567890";
    public LegacyExportPreflightTests()
    {
        Directory.CreateDirectory(Data);
        Directory.CreateDirectory(Config);
        File.WriteAllText(Path.Combine(Data, "masterDb.bz"), "original compressed bytes are not parsed by metadata diagnosis");
        File.WriteAllText(Path.Combine(Config, "init.yaml"), "devkey: " + Key + "\n");
    }

    LegacyExportPreflightResult Inspect(CancellationToken cancellation = default) => LegacyExportPreflight.Inspect(Data, Config, cancellation);

    [Fact]
    public void Readability_never_claims_quiescence_or_creates_an_archive()
    {
        var before = Snapshot();
        var result = Inspect();
        Assert.Equal("READABLE_SOURCE_METADATA", result.Status);
        Assert.Equal("complete", result.Phase);
        Assert.Null(result.FailureCode);
        Assert.Equal(2, result.Files);
        Assert.Equal(Directory.GetFiles(root, "*", SearchOption.AllDirectories).Sum(p => new FileInfo(p).Length), result.SourceBytes);
        Assert.False(result.QuiescenceVerified);
        Assert.False(result.ProductionReady);
        Assert.Equal(before, Snapshot());
        Assert.Equal(2, Directory.GetFiles(root, "*", SearchOption.AllDirectories).Length);
    }

    [Fact]
    public void Invalid_key_returns_fixed_code_without_private_values()
    {
        File.WriteAllText(Path.Combine(Config, "init.yaml"), "devkey: private key that must not appear in result\n");
        var result = Inspect();
        Assert.Equal("REFUSED", result.Status);
        Assert.Equal("configuration", result.Phase);
        Assert.Equal("LAYOUT_REFUSED", result.FailureCode);
        Assert.DoesNotContain("private key", JsonSerializer.Serialize(result));
        Assert.DoesNotContain(root, JsonSerializer.Serialize(result));
    }

    [Fact]
    public void Missing_configuration_reports_its_phase_without_a_path()
    {
        File.Delete(Path.Combine(Config, "init.yaml"));
        var result = Inspect();
        Assert.Equal("configuration", result.Phase);
        Assert.Equal("LAYOUT_REFUSED", result.FailureCode);
        Assert.DoesNotContain(root, JsonSerializer.Serialize(result));
    }

    [Fact]
    public void Missing_master_is_explicitly_refused()
    {
        File.Delete(Path.Combine(Data, "masterDb.bz"));
        var result = Inspect();
        Assert.Equal("inventory", result.Phase);
        Assert.Equal("MASTER_MISSING", result.FailureCode);
        Assert.Null(result.Files);
    }

    [Fact]
    public void Unsafe_source_name_does_not_leak_through_error_message()
    {
        File.WriteAllText(Path.Combine(Data, "private:tracker-passkey"), Key);
        var result = Inspect();
        Assert.Equal("inventory", result.Phase);
        Assert.Equal("LAYOUT_REFUSED", result.FailureCode);
        var json = JsonSerializer.Serialize(result);
        Assert.DoesNotContain("tracker-passkey", json);
        Assert.DoesNotContain(Key, json);
        Assert.DoesNotContain(root, json);
    }

    [Fact]
    public void Symlink_source_is_refused_without_following_it()
    {
        string source = Path.Combine(Data, "linked");
        File.CreateSymbolicLink(source, Path.Combine(Config, "init.yaml"));
        var result = Inspect();
        Assert.Equal("LAYOUT_REFUSED", result.FailureCode);
        Assert.Equal("inventory", result.Phase);
        Assert.Equal("devkey: " + Key + "\n", File.ReadAllText(Path.Combine(Config, "init.yaml")));
    }

    [Fact]
    public void Cancellation_has_fixed_code_and_no_filesystem_mutation()
    {
        var before = Snapshot();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        Assert.Equal("CANCELLED", Inspect(cancellation.Token).FailureCode);
        Assert.Equal(before, Snapshot());
        Assert.Throws<OperationCanceledException>(() => LegacyArchive.Inventory(Data, Config,
            LegacyArchive.DefaultByteLimit, LegacyArchive.DefaultFileLimit, cancellation.Token));
    }

    string[] Snapshot() => Directory.GetFiles(root, "*", SearchOption.AllDirectories).Order()
        .Select(p => p + ":" + Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))) + ":" + File.GetLastWriteTimeUtc(p).Ticks).ToArray();
    public void Dispose() => Directory.Delete(root, recursive: true);
}
