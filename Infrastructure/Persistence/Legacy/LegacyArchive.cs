using System;
using System.Collections.Generic;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Legacy;

internal sealed record LegacyArchiveFile(string Path, long Bytes, string Sha256);
internal sealed record LegacyArchiveResult(string Path, string Sha256, long Bytes,
    IReadOnlyList<LegacyArchiveFile> Files);

/// <summary>Original bytes only. Caller must stop every writer before using this primitive.</summary>
internal static class LegacyArchive
{
    internal const long DefaultByteLimit = 256L * 1024 * 1024;
    internal const int DefaultFileLimit = 10000;
    [DllImport("libc", EntryPoint = "open", SetLastError = true)]
    static extern int Open(string path, int flags);

    internal static FileStream OpenSource(string path)
    {
        // O_NONBLOCK prevents a FIFO from stalling startup. O_NOFOLLOW closes
        // the leaf-link check/open race. Parent directories must be trusted.
        int flags = OperatingSystem.IsMacOS() ? 0x4 | 0x100 | 0x1000000
            : OperatingSystem.IsLinux() ? 0x800 | 0x20000 | 0x80000
            : throw new PlatformNotSupportedException("Legacy export supports macOS/Linux only");
        var fd = Open(path, flags);
        if (fd < 0) throw new IOException("Cannot open legacy source safely");
        var handle = new SafeFileHandle((IntPtr)fd, ownsHandle: true);
        try
        {
            var stream = new FileStream(handle, FileAccess.Read);
            if (!stream.CanSeek) { stream.Dispose(); throw new InvalidDataException("Legacy export refuses non-seekable sources"); }
            return stream;
        }
        catch { handle.Dispose(); throw; }
    }

    static async Task<string> HashExact(FileStream stream, long expected, CancellationToken cancellation)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[64 * 1024];
        long read = 0;
        while (true)
        {
            int count = await stream.ReadAsync(buffer.AsMemory(0, (int)Math.Min(buffer.Length, expected - read + 1)), cancellation);
            if (count == 0) break;
            read += count;
            if (read > expected) throw new InvalidDataException("Legacy source exceeds inventoried length");
            hash.AppendData(buffer, 0, count);
        }
        if (read != expected) throw new InvalidDataException("Legacy source shorter than inventory");
        return Convert.ToHexStringLower(hash.GetHashAndReset());
    }
    internal static void NoLinks(string path)
    {
        for (var current = new FileInfo(System.IO.Path.GetFullPath(path)) as FileSystemInfo;
             current != null; current = Directory.GetParent(current.FullName))
            if (current.LinkTarget != null || (current.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("Legacy export refuses symbolic links");
    }

    internal static IReadOnlyList<(string Name, string Source, long Length, long Modified)> Inventory(
        string dataRoot, string configRoot, long byteLimit, int fileLimit)
    {
        if (byteLimit < 1 || byteLimit > DefaultByteLimit || fileLimit < 1 || fileLimit > DefaultFileLimit)
            throw new InvalidDataException("Invalid legacy export bounds");
        var data = System.IO.Path.GetFullPath(dataRoot);
        var config = System.IO.Path.GetFullPath(configRoot);
        if (data == config || data.StartsWith(config + System.IO.Path.DirectorySeparatorChar, StringComparison.Ordinal)
            || config.StartsWith(data + System.IO.Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("Legacy export roots must be separate");
        var files = new List<(string, string, long, long)>();
        long total = 0;
        int directories = 0;
        foreach (var root in new[] { ("Data", data), ("config", config) })
        {
            NoLinks(root.Item2);
            if (!Directory.Exists(root.Item2)) throw new InvalidDataException("Legacy export source missing");
            var pending = new Stack<string>();
            pending.Push(root.Item2);
            if (++directories > fileLimit) throw new InvalidDataException("Legacy export directory limit exceeded");
            while (pending.Count > 0)
            {
                foreach (var item in Directory.EnumerateFileSystemEntries(pending.Pop()))
                {
                    NoLinks(item);
                    var name = root.Item1 + "/" + System.IO.Path.GetRelativePath(root.Item2, item).Replace(System.IO.Path.DirectorySeparatorChar, '/');
                    if (name.Length > 1024 || name.Any(c => char.IsControl(c) || c == '\\' || c == ':'))
                        throw new InvalidDataException("Unsafe legacy export member name");
                    if (Directory.Exists(item))
                    {
                        if (++directories > fileLimit) throw new InvalidDataException("Legacy export directory limit exceeded");
                        pending.Push(item); continue;
                    }
                    var info = new FileInfo(item);
                    if (!info.Exists) throw new InvalidDataException("Legacy export member disappeared");
                    total = checked(total + info.Length);
                    if (total > byteLimit || files.Count >= fileLimit)
                        throw new InvalidDataException("Legacy export capacity limit exceeded");
                    files.Add((name, item, info.Length, info.LastWriteTimeUtc.Ticks));
                }
            }
        }
        return files.OrderBy(x => x.Item1, StringComparer.Ordinal).ToArray();
    }

    internal static async Task<LegacyArchiveResult> Create(string dataRoot, string configRoot,
        string privateDirectory, string sourceSha, CancellationToken cancellation,
        long byteLimit = DefaultByteLimit, int fileLimit = DefaultFileLimit)
    {
        // The directory must already be exclusively owned and private. Refuse
        // any existing output and never create/write anything in source roots.
        NoLinks(privateDirectory);
        if (!OperatingSystem.IsWindows() && (File.GetUnixFileMode(privateDirectory) &
            (UnixFileMode.GroupRead | UnixFileMode.GroupWrite | UnixFileMode.GroupExecute |
             UnixFileMode.OtherRead | UnixFileMode.OtherWrite | UnixFileMode.OtherExecute)) != 0)
            throw new InvalidDataException("Legacy export output directory must be private");
        var outputRoot = System.IO.Path.GetFullPath(privateDirectory);
        foreach (var source in new[] { dataRoot, configRoot }.Select(System.IO.Path.GetFullPath))
            if (outputRoot == source || outputRoot.StartsWith(source + System.IO.Path.DirectorySeparatorChar, StringComparison.Ordinal))
                throw new InvalidDataException("Legacy export output must be outside sources");
        if (sourceSha.Length != 40 || sourceSha.Any(c => !Uri.IsHexDigit(c)))
            throw new InvalidDataException("Legacy export requires a build source identity");
        var inventory = Inventory(dataRoot, configRoot, byteLimit, fileLimit);
        if (!inventory.Any(x => x.Name == "Data/masterDb.bz") || !inventory.Any(x => x.Name == "config/init.yaml"))
            throw new InvalidDataException("Legacy export requires original master and configuration");
        var output = System.IO.Path.Combine(outputRoot, "legacy.tar.gz");
        var files = new List<LegacyArchiveFile>();
        bool created = false;
        try
        {
            var options = new FileStreamOptions {
                Mode = FileMode.CreateNew, Access = FileAccess.Write, Share = FileShare.None,
                Options = FileOptions.Asynchronous };
            if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            await using (var raw = new FileStream(output, options))
            {
                created = true;
                await using (var gzip = new GZipStream(raw, CompressionLevel.Fastest, leaveOpen: true))
                await using (var tar = new TarWriter(gzip, TarEntryFormat.Pax, leaveOpen: true))
                {
                    foreach (var item in inventory)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        NoLinks(item.Source);
                        await using var source = OpenSource(item.Source);
                        var digest = await HashExact(source, item.Length, cancellation);
                        if (source.Length != item.Length) throw new InvalidDataException("Legacy source changed during export");
                        source.Position = 0;
                        var entry = new PaxTarEntry(TarEntryType.RegularFile, item.Name) {
                            DataStream = source, Mode = UnixFileMode.UserRead | UnixFileMode.UserWrite,
                            Uid = 0, Gid = 0, UserName = "", GroupName = "" };
                        await tar.WriteEntryAsync(entry, cancellation);
                        files.Add(new(item.Name, item.Length, digest));
                    }
                    // Re-read original bytes and topology after every tar entry.
                    // This detects concurrent changes but is NOT a substitute for
                    // a controller-verified single-worker stopped-writer window.
                    var after = Inventory(dataRoot, configRoot, byteLimit, fileLimit);
                    if (!inventory.SequenceEqual(after)) throw new InvalidDataException("Legacy source changed during export");
                    var byName = after.ToDictionary(x => x.Name, StringComparer.Ordinal);
                    foreach (var file in files)
                    {
                        var item = byName[file.Path];
                        await using var source = OpenSource(item.Source);
                        if (await HashExact(source, file.Bytes, cancellation) != file.Sha256)
                            throw new InvalidDataException("Legacy source checksum changed during export");
                    }
                    var manifest = JsonSerializer.SerializeToUtf8Bytes(new {
                        schema = "jacred-legacy-byte-archive/v1", source_sha = sourceSha,
                        quiescence = "REQUIRES_VERIFIED_SINGLE_WORKER_REPLACEMENT",
                        files = files.Select(f => new { path = f.Path, bytes = f.Bytes, sha256 = f.Sha256 }) });
                    using var manifestStream = new MemoryStream(manifest, writable: false);
                    await tar.WriteEntryAsync(new PaxTarEntry(TarEntryType.RegularFile, "manifest.json") {
                        DataStream = manifestStream, Mode = UnixFileMode.UserRead | UnixFileMode.UserWrite }, cancellation);
                }
                await raw.FlushAsync(cancellation);
                raw.Flush(flushToDisk: true);
            }
            await using var archive = File.OpenRead(output);
            return new(output, Convert.ToHexStringLower(await SHA256.HashDataAsync(archive, cancellation)), archive.Length, files);
        }
        catch
        {
            if (created) File.Delete(output);
            throw;
        }
    }
}
