using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace JacRed.Infrastructure.Persistence.Legacy;

/// <summary>Separate process mode: no normal configuration singleton, FileDB or hosted workers.</summary>
internal static class LegacyExportHost
{
    internal static string ReadManagedKey(string configRoot)
    {
        string path = Path.Combine(configRoot, "init.yaml");
        LegacyArchive.NoLinks(path);
        if (new FileInfo(path).Length > 1024 * 1024) throw new InvalidDataException("Legacy configuration exceeds limit");
        var lines = File.ReadLines(path).Where(x => x.StartsWith("devkey:", StringComparison.Ordinal)).ToArray();
        if (lines.Length != 1) throw new InvalidDataException("Legacy exporter requires one managed administrative key");
        var match = Regex.Match(lines[0], "^devkey:[ \\t]*(?:\"(?<key>[A-Za-z0-9_-]{24,128})\"|'(?<key>[A-Za-z0-9_-]{24,128})'|(?<key>[A-Za-z0-9_-]{24,128}))[ \\t]*(?:#.*)?$",
            RegexOptions.CultureInvariant, TimeSpan.FromSeconds(1));
        if (!match.Success) throw new InvalidDataException("Legacy exporter requires a managed administrative key");
        return match.Groups["key"].Value;
    }

    internal static int Run()
    {
        string privateDirectory = null;
        try
        {
            if (!OperatingSystem.IsMacOS() && !OperatingSystem.IsLinux())
                throw new PlatformNotSupportedException("Legacy export supports macOS/Linux only");
            if (Environment.GetEnvironmentVariable("JACRED_CATALOG_BACKEND") is { } backend && backend != "filedb")
                throw new InvalidDataException("Legacy exporter refuses SQL mode");
            if (!long.TryParse(Environment.GetEnvironmentVariable("JACRED_LEGACY_EXPORT_DEADLINE_UNIX"),
                NumberStyles.None, CultureInfo.InvariantCulture, out long expiry))
                throw new InvalidDataException("Legacy export deadline required");
            var remaining = DateTimeOffset.FromUnixTimeSeconds(expiry) - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero || remaining > TimeSpan.FromMinutes(10))
                throw new InvalidDataException("Legacy export deadline outside bounded window");
            using var deadline = new CancellationTokenSource(remaining);
            var data = Environment.GetEnvironmentVariable("JACRED_LEGACY_EXPORT_DATA_ROOT") ?? "/app/Data";
            var config = Environment.GetEnvironmentVariable("JACRED_CONFIG_DIR") ?? "/app/config";
            var keyHash = SHA256.HashData(Encoding.UTF8.GetBytes(ReadManagedKey(config)));
            string temporaryRoot = OperatingSystem.IsMacOS() ? "/private/tmp" : Path.GetTempPath();
            privateDirectory = Path.Combine(temporaryRoot, "jacred-legacy-" + Guid.NewGuid().ToString("N"));
            if (OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
            Directory.CreateDirectory(privateDirectory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
            using var preparation = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token);
            preparation.CancelAfter(TimeSpan.FromSeconds(90));
            var archive = LegacyArchive.Create(data, config, privateDirectory, VersionInfo.GitFullSha, preparation.Token).GetAwaiter().GetResult();
            var builder = WebApplication.CreateSlimBuilder(Array.Empty<string>());
            builder.Logging.ClearProviders();
            var ip = IPAddress.Parse(Environment.GetEnvironmentVariable("JACRED_LEGACY_EXPORT_LISTEN_IP") ?? "0.0.0.0");
            var port = int.Parse(Environment.GetEnvironmentVariable("JACRED_LEGACY_EXPORT_PORT") ?? "9117", CultureInfo.InvariantCulture);
            if (port < 1024 || port > 65535) throw new InvalidDataException("Invalid export port");
            builder.WebHost.UseKestrel(o => { o.Listen(ip, port); o.Limits.MaxRequestBodySize = 0; });
            var app = builder.Build();
            using var expiration = deadline.Token.Register(app.Lifetime.StopApplication);
            using var download = new SemaphoreSlim(1, 1);
            int attempts = 0;
            app.Use(async (context, next) => {
                context.Response.Headers.CacheControl = "private, no-store";
                context.Response.Headers.Pragma = "no-cache";
                context.Response.Headers.XContentTypeOptions = "nosniff";
                if (context.Request.Path != "/health" && context.Request.Path != "/version")
                {
                    var supplied = context.Request.Headers["X-Dev-Key"];
                    if (supplied.Count != 1 || supplied[0].Length > 128 ||
                        !CryptographicOperations.FixedTimeEquals(keyHash, SHA256.HashData(Encoding.UTF8.GetBytes(supplied[0]))))
                    { context.Response.StatusCode = 401; return; }
                }
                await next();
            });
            app.MapGet("/health", () => Results.Json(new { status = "legacy-export-only", productionReady = false }));
            app.MapGet("/version", () => Results.Json(new { gitFullSha = VersionInfo.GitFullSha, mode = "legacy-export-only", catalogBackend = "filedb" }));
            app.MapGet("/migration/archive", async context => {
                if (!await download.WaitAsync(0, context.RequestAborted)) { context.Response.StatusCode = 429; return; }
                try
                {
                    if (++attempts > 3) { context.Response.StatusCode = 429; return; }
                    using var transfer = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted, deadline.Token);
                    transfer.CancelAfter(TimeSpan.FromSeconds(90));
                    context.Response.ContentType = "application/gzip";
                    context.Response.ContentLength = archive.Bytes;
                    context.Response.Headers["X-JacRed-Archive-Sha256"] = archive.Sha256;
                    context.Response.Headers["X-JacRed-Source-Sha"] = VersionInfo.GitFullSha;
                    await using var stream = File.OpenRead(archive.Path);
                    await stream.CopyToAsync(context.Response.Body, transfer.Token);
                }
                catch (OperationCanceledException) { context.Abort(); }
                finally { download.Release(); }
            });
            app.Run();
            app.DisposeAsync().AsTask().GetAwaiter().GetResult();
            return 0;
        }
        catch
        {
            // Config and paths can contain private data. Never print exceptions.
            Console.Error.WriteLine("Legacy export refused or failed; private details omitted");
            return 1;
        }
        finally
        {
            if (privateDirectory != null && Directory.Exists(privateDirectory)) Directory.Delete(privateDirectory, recursive: true);
        }
    }
}
