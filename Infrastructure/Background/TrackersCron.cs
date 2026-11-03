using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using JacRed.Infrastructure.Persistence;
using JacRed.Infrastructure.Logging;
using JacRed.Infrastructure.Networking;

namespace JacRed.Infrastructure.Background
{
    public static class TrackersCron
    {
        async public static Task Run(CancellationToken cancellationToken = default)
        {
            await Task.Delay(20_000, cancellationToken);

            while (!cancellationToken.IsCancellationRequested)
            {
                if (!AppInit.conf.evercache.enable || AppInit.conf.evercache.validHour > 0)
                {
                    await Task.Delay(TimeSpan.FromMinutes(1), cancellationToken);
                    continue;
                }

                await Task.Delay(TimeSpan.FromHours(1), cancellationToken);

                try
                {
                    JacRedLog.Information(JacRedLogCategories.Trackers, $"start / {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
                    HashSet<string> trackers = new HashSet<string>();
                    HashSet<string> attempted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    using var probeDeadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                    probeDeadline.CancelAfter(TimeSpan.FromMinutes(2));

                    foreach (var item in FileDB.masterDb.ToArray())
                    {
                        if (probeDeadline.IsCancellationRequested || attempted.Count >= 128) break;
                        foreach (var t in FileDB.OpenRead(item.Key/*, cache: false*/).Values)
                        {
                            if (probeDeadline.IsCancellationRequested || attempted.Count >= 128) break;
                            if (string.IsNullOrEmpty(t.magnet))
                                continue;

                            try
                            {
                                if (t.magnet.Contains("&"))
                                {
                                    foreach (Match tr in Regex.Matches(t.magnet, "(?:[?&])tr=([^&]+)"))
                                    {
                                        if (probeDeadline.IsCancellationRequested || attempted.Count >= 128) break;
                                        if (!PublicTrackerProbe.TryNormalize(tr.Groups[1].Value, out var endpoint)) continue;
                                        string tracker = endpoint.AbsoluteUri;
                                        if (!attempted.Add(tracker)) continue;
                                        if (await PublicTrackerProbe.CheckAsync(endpoint, probeDeadline.Token)) trackers.Add(tracker);
                                    }
                                }
                            }
                            catch { }
                        }
                    }

                    cancellationToken.ThrowIfCancellationRequested();
                    Directory.CreateDirectory("wwwroot");
                    File.WriteAllLines("wwwroot/trackers.txt", trackers);
                    JacRedLog.Information(JacRedLogCategories.Trackers, $"end / {DateTime.Now:yyyy-MM-dd HH:mm:ss} wrote {trackers.Count} trackers to wwwroot/trackers.txt");
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { break; }
                catch (Exception ex) { JacRedLog.Error(JacRedLogCategories.Trackers, $"error / {ex.Message}"); }
            }
        }

    }
}
