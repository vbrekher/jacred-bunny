using System;
using System.IO;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using JacRed.Infrastructure.Persistence;
using JacRed.Infrastructure.Persistence.Legacy;
using JacRed.Infrastructure.Persistence.Native;
using JacRed.Infrastructure.Trackers;
using Microsoft.Extensions.Hosting;

namespace JacRed.Infrastructure.Background;

// Register FIRST: generic-host shutdown stops services in reverse order, so
// Kestrel and every normal hosted worker stop before this checkpoint is written.
internal sealed class LegacyCheckpointWorker : IHostedService
{
    public LegacyCheckpointWorker() { }
    internal const string ReceiptPath = "Data/temp/.jacred-bunny-checkpoint.json";
    internal static string BootId { get; } = Guid.NewGuid().ToString("N");
    internal static bool Ready { get; private set; }

    internal static bool IsOwnedReceipt(string path)
    {
        if (new FileInfo(path).Length > 4096) return false;
        using var document = JsonDocument.Parse(File.ReadAllBytes(path));
        return document.RootElement.TryGetProperty("schema", out var schema) && schema.GetString() == "jacred-legacy-shutdown/v1" &&
            document.RootElement.TryGetProperty("owner", out var owner) && owner.GetString() == "jacred-bunny";
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        Ready = false;
        if (NativeCatalogRuntime.Enabled) return Task.CompletedTask;
        try
        {
            LegacyArchive.NoLinks("Data/temp");
            if (File.Exists(ReceiptPath) || new FileInfo(ReceiptPath).LinkTarget != null)
            {
                LegacyArchive.NoLinks(ReceiptPath);
                if (!IsOwnedReceipt(ReceiptPath)) return Task.CompletedTask;
                File.Delete(ReceiptPath);
            }
            Ready = true;
        }
        catch { /* Normal service remains available; export readiness stays false. */ }
        return Task.CompletedTask;
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        if (!Ready || NativeCatalogRuntime.Enabled) return;
        string temporary = ReceiptPath + "." + BootId;
        try
        {
            // Tracker helpers cancel on ApplicationStopping. Do not attest a
            // checkpoint while an async operation can still modify the catalog.
            while (TrackerSyncHelpers.GetActiveJobs().Count != 0)
                await Task.Delay(50, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            var proof = FileDB.VerifiedLegacyShutdownFlush();
            cancellationToken.ThrowIfCancellationRequested();
            var options = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write, Share = FileShare.None };
            if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            using (var stream = new FileStream(temporary, options))
            {
                JsonSerializer.Serialize(stream, new
                {
                    schema = "jacred-legacy-shutdown/v1",
                    owner = "jacred-bunny",
                    boot_id = BootId,
                    source_sha = VersionInfo.GitFullSha,
                    master_sha256 = proof.MasterSha256,
                    master_keys = proof.Keys,
                    checkpointed_utc = DateTime.UtcNow.ToString("O")
                });
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, ReceiptPath, overwrite: false);
        }
        catch
        {
            Console.Error.WriteLine("Legacy shutdown checkpoint not confirmed; migration must remain blocked");
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }
}
