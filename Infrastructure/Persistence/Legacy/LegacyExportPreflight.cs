using System;
using System.IO;
using System.Linq;
using System.Threading;

namespace JacRed.Infrastructure.Persistence.Legacy;

internal sealed record LegacyExportPreflightResult(string Status, string Phase, string FailureCode,
    int? Files = null, long? SourceBytes = null, bool QuiescenceVerified = false, bool ProductionReady = false);

/// <summary>Read-only source diagnosis. Never returns filenames, keys or exception messages.</summary>
internal static class LegacyExportPreflight
{
    internal static LegacyExportPreflightResult Inspect(string data, string config, CancellationToken cancellation)
    {
        string phase = "configuration";
        try
        {
            cancellation.ThrowIfCancellationRequested();
            _ = LegacyExportHost.ReadManagedKey(config);
            phase = "inventory";
            var inventory = LegacyArchive.Inventory(data, config, LegacyArchive.DefaultByteLimit,
                LegacyArchive.DefaultFileLimit, cancellation);
            if (!inventory.Any(x => x.Name.StartsWith("Data/masterDb", StringComparison.Ordinal) &&
                x.Name.EndsWith(".bz", StringComparison.Ordinal) && x.Name.Count(c => c == '/') == 1))
                return new("REFUSED", phase, "MASTER_MISSING");
            phase = "source_open";
            foreach (var file in inventory)
            {
                cancellation.ThrowIfCancellationRequested();
                using var stream = LegacyArchive.OpenSource(file.Source);
                if (stream.Length != file.Length) return new("REFUSED", phase, "SOURCE_CHANGED");
                // Verify actual read permission without hashing a live catalog or creating an archive.
                if (file.Length != 0 && stream.ReadByte() == -1) return new("REFUSED", phase, "SOURCE_CHANGED");
            }
            return new("READABLE_SOURCE_METADATA", "complete", null, inventory.Count, inventory.Sum(x => x.Length));
        }
        catch (OperationCanceledException) { return new("REFUSED", phase, "CANCELLED"); }
        catch (UnauthorizedAccessException) { return new("REFUSED", phase, "ACCESS_REFUSED"); }
        catch (InvalidDataException) { return new("REFUSED", phase, "LAYOUT_REFUSED"); }
        catch (IOException) { return new("REFUSED", phase, "IO_REFUSED"); }
        catch { return new("REFUSED", phase, "INSPECTION_FAILED"); }
    }
}
