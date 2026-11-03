using System.Formats.Tar;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Diagnostics;
using JacRed.Infrastructure.Persistence.Legacy;
using Xunit;

namespace JacRed.Tests.Persistence.Legacy;

public sealed class LegacyArchiveTests : IDisposable
{
    readonly string root = Path.Combine(Directory.GetCurrentDirectory(), ".legacy-test-" + Guid.NewGuid().ToString("N"));
    string Data => Path.Combine(root, "Data");
    string Config => Path.Combine(root, "config");
    string Private => Path.Combine(root, "private");
    const string Source = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const string Key = "dummy_administrative_key_1234567890";

    public LegacyArchiveTests()
    {
        Directory.CreateDirectory(Data);
        Directory.CreateDirectory(Config);
        Directory.CreateDirectory(Private);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(Private, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        File.WriteAllBytes(Path.Combine(Data, "masterDb.bz"), new byte[] { 31, 139, 0, 255 });
        File.WriteAllText(Path.Combine(Config, "init.yaml"), "devkey: \"" + Key + "\"\napikey: dummy-search-key\n");
    }

    [Fact]
    public async Task Archive_preserves_original_bytes_tracks_config_jobs_and_hashes()
    {
        Directory.CreateDirectory(Path.Combine(Data, "tracks"));
        Directory.CreateDirectory(Path.Combine(Data, "temp"));
        File.WriteAllBytes(Path.Combine(Data, "tracks", "probe.json"), new byte[] { 0, 1, 255, 128 });
        File.WriteAllText(Path.Combine(Data, "temp", "resume.json"), "{\"cursor\":7}");
        File.WriteAllText(Path.Combine(Data, "日本語 # fixture"), "");
        var result = await LegacyArchive.Create(Data, Config, Private, Source, default);
        Assert.Equal(5, result.Files.Count);
        Assert.Equal(result.Sha256, Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(result.Path))));
        if (!OperatingSystem.IsWindows()) Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite, File.GetUnixFileMode(result.Path));
        using var gzip = new GZipStream(File.OpenRead(result.Path), CompressionMode.Decompress);
        using var tar = new TarReader(gzip);
        var content = new Dictionary<string, byte[]>();
        TarEntry entry;
        while ((entry = tar.GetNextEntry()) != null)
        {
            Assert.Equal(TarEntryType.RegularFile, entry.EntryType);
            using var memory = new MemoryStream();
            entry.DataStream?.CopyTo(memory);
            content.Add(entry.Name, memory.ToArray());
        }
        foreach (var file in result.Files)
        {
            Assert.Equal(file.Bytes, content[file.Path].LongLength);
            Assert.Equal(file.Sha256, Convert.ToHexStringLower(SHA256.HashData(content[file.Path])));
            string source = file.Path.StartsWith("Data/") ? Path.Combine(Data, file.Path[5..]) : Path.Combine(Config, file.Path[7..]);
            Assert.Equal(File.ReadAllBytes(source), content[file.Path]);
        }
        using var manifest = JsonDocument.Parse(content["manifest.json"]);
        Assert.Equal(Source, manifest.RootElement.GetProperty("source_sha").GetString());
        Assert.Equal("REQUIRES_VERIFIED_SINGLE_WORKER_REPLACEMENT", manifest.RootElement.GetProperty("quiescence").GetString());
        Assert.Equal(5, manifest.RootElement.GetProperty("files").GetArrayLength());
        Assert.False(Directory.Exists(Path.Combine(Data, "log"))); // No normal startup side effects.
    }

    [Fact]
    public async Task Existing_archive_is_not_overwritten_or_deleted()
    {
        string target = Path.Combine(Private, "legacy.tar.gz");
        File.WriteAllText(target, "preserve");
        await Assert.ThrowsAsync<IOException>(() => LegacyArchive.Create(Data, Config, Private, Source, default));
        Assert.Equal("preserve", File.ReadAllText(target));
    }

    [Fact]
    public async Task Cancellation_removes_partial_archive()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => LegacyArchive.Create(Data, Config, Private, Source, cancellation.Token));
        Assert.Empty(Directory.EnumerateFileSystemEntries(Private));
    }

    [Theory]
    [InlineData("byte")]
    [InlineData("file")]
    [InlineData("directory")]
    [InlineData("source")]
    public async Task Capacity_or_identity_failure_never_publishes_archive(string failure)
    {
        if (failure == "directory")
            for (int i = 0; i < 5; i++) Directory.CreateDirectory(Path.Combine(Data, "d" + i));
        if (failure == "file") File.WriteAllText(Path.Combine(Data, "extra"), "x");
        await Assert.ThrowsAsync<InvalidDataException>(() => LegacyArchive.Create(Data, Config, Private,
            failure == "source" ? "operator-assertion" : Source, default,
            byteLimit: failure == "byte" ? 1 : 1024 * 1024, fileLimit: failure is "file" or "directory" ? 2 : 100));
        Assert.Empty(Directory.EnumerateFileSystemEntries(Private));
    }

    [Theory]
    [InlineData("leaf")]
    [InlineData("directory")]
    [InlineData("output")]
    public async Task Symbolic_links_refused_without_touching_their_targets(string kind)
    {
        var external = Path.Combine(root, "external");
        Directory.CreateDirectory(external);
        File.WriteAllText(Path.Combine(external, "private"), "must-remain");
        string output = Private;
        if (kind == "leaf") File.CreateSymbolicLink(Path.Combine(Data, "leaf"), Path.Combine(external, "private"));
        if (kind == "directory") Directory.CreateSymbolicLink(Path.Combine(Data, "linked"), external);
        if (kind == "output") { output = Path.Combine(root, "output-link"); Directory.CreateSymbolicLink(output, external); }
        await Assert.ThrowsAsync<InvalidDataException>(() => LegacyArchive.Create(Data, Config, output, Source, default));
        Assert.Equal("must-remain", File.ReadAllText(Path.Combine(external, "private")));
        Assert.Single(Directory.EnumerateFileSystemEntries(external));
    }

    [Fact]
    public async Task Fifo_is_refused_without_blocking_or_partial_archive()
    {
        using var process = Process.Start(new ProcessStartInfo("mkfifo") { ArgumentList = { Path.Combine(Data, "pipe") }, UseShellExecute = false });
        Assert.True(process.WaitForExit(5000));
        Assert.Equal(0, process.ExitCode);
        await Assert.ThrowsAsync<InvalidDataException>(() => LegacyArchive.Create(Data, Config, Private, Source, default)).WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Empty(Directory.EnumerateFileSystemEntries(Private));
    }

    [Theory]
    [InlineData("plain")]
    [InlineData("double")]
    [InlineData("single")]
    public void Persisted_key_formats_read_without_loading_normal_configuration(string format)
    {
        string value = format == "double" ? "\"" + Key + "\"" : format == "single" ? "'" + Key + "'" : Key;
        File.WriteAllText(Path.Combine(Config, "init.yaml"), "devkey: " + value + " # current\n");
        Assert.Equal(Key, LegacyExportHost.ReadManagedKey(Config));
    }

    [Theory]
    [InlineData("apikey: unrelated")]
    [InlineData("devkey: short")]
    [InlineData("devkey: injected key value")]
    [InlineData("devkey: dummy_administrative_key_1234567890\ndevkey: dummy_administrative_key_1234567890")]
    public void Invalid_or_ambiguous_keys_fail_closed(string content)
    {
        File.WriteAllText(Path.Combine(Config, "init.yaml"), content);
        Assert.Throws<InvalidDataException>(() => LegacyExportHost.ReadManagedKey(Config));
    }

    public void Dispose() => Directory.Delete(root, recursive: true);
}
