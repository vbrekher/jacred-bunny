using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Legacy;

/// <summary>Private artifact handoff between a root-only reader and an unprivileged HTTP process.</summary>
internal static class LegacyPreparedArchive
{
    internal const string Schema = "jacred-private-export-handoff/v1";
    [DllImport("libc", EntryPoint = "geteuid")]
    internal static extern uint EffectiveUid();

    internal static bool Available => OperatingSystem.IsLinux() &&
        File.Exists("/usr/local/libexec/jacred-export") &&
        File.Exists("/usr/local/libexec/jacred-container-entry");

    static void PrivateMode(string path, bool directory)
    {
        LegacyArchive.NoLinks(path);
        if (OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        var expected = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        if (directory) expected |= UnixFileMode.UserExecute;
        if (File.GetUnixFileMode(path) != expected) throw new InvalidDataException("Unsafe private handoff permissions");
    }

    internal static string ValidateDirectory(string directory)
    {
        var path = Path.GetFullPath(directory);
        string parent = OperatingSystem.IsMacOS() ? "/private/tmp" : "/tmp";
        if (Path.GetDirectoryName(path) != parent ||
            !Regex.IsMatch(Path.GetFileName(path), "^jacred-legacy-handoff\\.[A-Za-z0-9]{10,64}$", RegexOptions.CultureInvariant))
            throw new InvalidDataException("Unexpected private handoff directory");
        PrivateMode(path, directory: true);
        return path;
    }

    internal static void Write(string directory, LegacyArchiveResult archive, string source, long expiry, byte[] keyHash)
    {
        var path = ValidateDirectory(directory);
        if (Path.GetFullPath(archive.Path) != Path.Combine(path, "legacy.tar.gz") || keyHash.Length != 32)
            throw new InvalidDataException("Invalid prepared artifact identity");
        PrivateMode(archive.Path, directory: false);
        if (OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        using var stream = new FileStream(Path.Combine(path, "prepared.json"), new FileStreamOptions {
            Mode = FileMode.CreateNew, Access = FileAccess.Write, Share = FileShare.None,
            UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite });
        JsonSerializer.Serialize(stream, new {
            schema = Schema, source_sha = source, expires_unix = expiry,
            archive_sha256 = archive.Sha256, archive_bytes = archive.Bytes,
            configuration_key_sha256 = Convert.ToHexStringLower(keyHash) });
        stream.Flush(flushToDisk: true);
    }

    internal static async Task<LegacyArchiveResult> Read(string directory, string source, long expiry,
        byte[] keyHash, CancellationToken cancellation)
    {
        if (keyHash.Length != 32) throw new InvalidDataException("Invalid private handoff key identity");
        var path = ValidateDirectory(directory);
        string[] names = Directory.EnumerateFileSystemEntries(path).Select(Path.GetFileName).Order(StringComparer.Ordinal).ToArray();
        if (!names.SequenceEqual(new[] { "legacy.tar.gz", "prepared.json" }))
            throw new InvalidDataException("Unexpected private handoff contents");
        var metadata = Path.Combine(path, "prepared.json");
        PrivateMode(metadata, directory: false);
        using var metadataStream = LegacyArchive.OpenSource(metadata);
        if (metadataStream.Length > 4096) throw new InvalidDataException("Private handoff exceeds metadata bound");
        var metadataBytes = new byte[4097];
        int count = await metadataStream.ReadAtLeastAsync(metadataBytes, metadataBytes.Length,
            throwOnEndOfStream: false, cancellationToken: cancellation);
        if (count > 4096) throw new InvalidDataException("Private handoff exceeds metadata bound");
        using var document = JsonDocument.Parse(metadataBytes.AsMemory(0, count));
        var value = document.RootElement;
        string[] expected = { "archive_bytes", "archive_sha256", "configuration_key_sha256", "expires_unix", "schema", "source_sha" };
        if (value.ValueKind != JsonValueKind.Object ||
            !value.EnumerateObject().Select(x => x.Name).Order(StringComparer.Ordinal).SequenceEqual(expected))
            throw new InvalidDataException("Unknown or duplicate private handoff fields");
        var digest = value.GetProperty("archive_sha256").GetString();
        if (value.GetProperty("schema").GetString() != Schema || value.GetProperty("source_sha").GetString() != source ||
            value.GetProperty("expires_unix").GetInt64() != expiry || expiry <= DateTimeOffset.UtcNow.ToUnixTimeSeconds() ||
            expiry > DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 600 ||
            !Regex.IsMatch(digest ?? "", "^[0-9a-f]{64}$", RegexOptions.CultureInvariant) ||
            value.GetProperty("configuration_key_sha256").GetString() != Convert.ToHexStringLower(keyHash))
            throw new InvalidDataException("Private handoff does not match the bounded source/key identity");
        long bytes = value.GetProperty("archive_bytes").GetInt64();
        if (bytes <= 0 || bytes > 288L * 1024 * 1024) throw new InvalidDataException("Private artifact exceeds bound");
        var archive = Path.Combine(path, "legacy.tar.gz");
        PrivateMode(archive, directory: false);
        await using var stream = LegacyArchive.OpenSource(archive);
        if (stream.Length != bytes || await LegacyArchive.HashExact(stream, bytes, cancellation) != digest)
            throw new InvalidDataException("Private artifact bytes changed after preparation");
        return new(archive, digest, bytes, Array.Empty<LegacyArchiveFile>());
    }

    internal static int Prepare(string directory)
    {
        try
        {
            if (!OperatingSystem.IsLinux() || EffectiveUid() != 0)
                throw new InvalidDataException("Privileged preparation requires the managed image bootstrap");
            var inputs = LegacyExportHost.ReadInputs();
            if (Path.GetFullPath(inputs.Data) != "/app/Data" || Path.GetFullPath(inputs.Config) != "/app/config")
                throw new InvalidDataException("Privileged reader is restricted to managed source roots");
            var path = ValidateDirectory(directory);
            if (Directory.EnumerateFileSystemEntries(path).Any()) throw new InvalidDataException("Private preparation output is not empty");
            using var deadline = new CancellationTokenSource(DateTimeOffset.FromUnixTimeSeconds(inputs.Expiry) - DateTimeOffset.UtcNow);
            using var preparation = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token);
            preparation.CancelAfter(TimeSpan.FromSeconds(90));
            var keyHash = SHA256.HashData(Encoding.UTF8.GetBytes(LegacyExportHost.ReadManagedKey(inputs.Config)));
            var archive = LegacyArchive.Create(inputs.Data, inputs.Config, path, VersionInfo.GitFullSha, preparation.Token).GetAwaiter().GetResult();
            preparation.Token.ThrowIfCancellationRequested();
            Write(path, archive, VersionInfo.GitFullSha, inputs.Expiry, keyHash);
            return 0;
        }
        catch
        {
            Console.Error.WriteLine("Private legacy preparation refused or failed; details omitted");
            return 1;
        }
    }
}
