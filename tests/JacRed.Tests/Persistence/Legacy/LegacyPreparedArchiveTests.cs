using System.Security.Cryptography;
using System.Runtime.Versioning;
using System.Text;
using System.Text.Json.Nodes;
using JacRed.Infrastructure.Persistence.Legacy;
using Xunit;

namespace JacRed.Tests.Persistence.Legacy;

[UnsupportedOSPlatform("windows")]
public sealed class LegacyPreparedArchiveTests : IDisposable
{
    readonly string directory = Path.Combine(OperatingSystem.IsMacOS() ? "/private/tmp" : "/tmp",
        "jacred-legacy-handoff." + Guid.NewGuid().ToString("N"));
    const string Source = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    readonly byte[] key = SHA256.HashData(Encoding.UTF8.GetBytes("synthetic_administrative_key_1234567890"));
    readonly long expiry = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60;
    string Archive => Path.Combine(directory, "legacy.tar.gz");
    string Metadata => Path.Combine(directory, "prepared.json");
    public LegacyPreparedArchiveTests()
    {
        Directory.CreateDirectory(directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        File.WriteAllText(Archive, "synthetic private archive bytes");
        File.SetUnixFileMode(Archive, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }
    void Write() => LegacyPreparedArchive.Write(directory, new(Archive,
        Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(Archive))), new FileInfo(Archive).Length,
        Array.Empty<LegacyArchiveFile>()), Source, expiry, key);
    void Mutate(string field, JsonNode value)
    {
        var data = JsonNode.Parse(File.ReadAllText(Metadata));
        data[field] = value;
        File.WriteAllText(Metadata, data.ToJsonString());
    }
    Task<LegacyArchiveResult> Read() => LegacyPreparedArchive.Read(directory, Source, expiry, key, default);

    [Fact]
    public async Task Private_handoff_binds_exact_source_key_expiry_and_bytes_without_modification()
    {
        Write();
        var before = Directory.GetFiles(directory).Order().Select(File.ReadAllText).ToArray();
        var result = await Read();
        Assert.Equal(Archive, result.Path);
        Assert.Equal(new FileInfo(Archive).Length, result.Bytes);
        Assert.Equal(before, Directory.GetFiles(directory).Order().Select(File.ReadAllText));
        Assert.DoesNotContain("synthetic_administrative_key", File.ReadAllText(Metadata));
    }

    [Theory]
    [InlineData("source_sha", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")]
    [InlineData("schema", "foreign-owner/v1")]
    [InlineData("configuration_key_sha256", "invalid")]
    [InlineData("archive_sha256", "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")]
    public async Task Mismatched_handoff_is_refused_without_removing_artifacts(string name, string value)
    {
        Write(); Mutate(name, JsonValue.Create(value));
        await Assert.ThrowsAsync<InvalidDataException>(Read);
        Assert.True(File.Exists(Archive));
        Assert.True(File.Exists(Metadata));
    }

    [Fact]
    public async Task Changed_archive_with_same_length_cannot_pass_its_receipt()
    {
        Write();
        var bytes = File.ReadAllBytes(Archive); bytes[0] ^= 1; File.WriteAllBytes(Archive, bytes);
        await Assert.ThrowsAsync<InvalidDataException>(Read);
    }

    [Fact]
    public async Task Deadline_cannot_be_changed_in_the_handoff()
    {
        Write(); Mutate("expires_unix", JsonValue.Create(expiry + 1));
        await Assert.ThrowsAsync<InvalidDataException>(Read);
    }

    [Fact]
    public async Task Duplicate_and_unknown_metadata_fields_are_rejected()
    {
        Write(); var original = File.ReadAllText(Metadata);
        File.WriteAllText(Metadata, original[..^1] + ",\"schema\":\"" + LegacyPreparedArchive.Schema + "\"}");
        await Assert.ThrowsAsync<InvalidDataException>(Read);
        File.WriteAllText(Metadata, original[..^1] + ",\"unexpected\":true}");
        await Assert.ThrowsAsync<InvalidDataException>(Read);
    }

    [Fact]
    public async Task Foreign_directory_contents_are_preserved_and_refused()
    {
        Write(); File.WriteAllText(Path.Combine(directory, "preserve-me"), "foreign bytes");
        await Assert.ThrowsAsync<InvalidDataException>(Read);
        Assert.Equal("foreign bytes", File.ReadAllText(Path.Combine(directory, "preserve-me")));
    }

    [Fact]
    public async Task Linked_archive_is_never_followed()
    {
        Write(); File.Move(Archive, Path.Combine(directory, "original"));
        File.CreateSymbolicLink(Archive, Path.Combine(directory, "original"));
        // Remove the extra name by using an external, still-private source.
        string outside = directory + ".source";
        File.Move(Path.Combine(directory, "original"), outside);
        File.Delete(Archive); File.CreateSymbolicLink(Archive, outside);
        try { await Assert.ThrowsAsync<InvalidDataException>(Read); }
        finally { File.Delete(outside); }
    }

    [Fact]
    public async Task Non_private_metadata_is_refused()
    {
        Write(); File.SetUnixFileMode(Metadata, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.GroupRead);
        await Assert.ThrowsAsync<InvalidDataException>(Read);
    }

    [Fact]
    public void Existing_handoff_is_never_overwritten()
    {
        Write(); var original = File.ReadAllBytes(Metadata);
        Assert.Throws<IOException>(Write);
        Assert.Equal(original, File.ReadAllBytes(Metadata));
    }

    [Fact]
    public async Task Oversized_metadata_is_rejected_before_parsing()
    {
        Write(); File.WriteAllText(Metadata, new string(' ', 4097));
        await Assert.ThrowsAsync<InvalidDataException>(Read);
    }

    [Fact]
    public async Task Cancellation_never_removes_the_prepared_copy()
    {
        Write(); using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => LegacyPreparedArchive.Read(directory, Source, expiry, key, cancelled.Token));
        Assert.True(File.Exists(Archive));
    }
    public void Dispose() => Directory.Delete(directory, recursive: true);
}
