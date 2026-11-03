using System;
using System.Threading;
using System.Threading.Tasks;
using JacRed.Infrastructure.Persistence.Native;
using Microsoft.AspNetCore.Mvc;

namespace JacRed.Controllers;

public class InternalVersionController : Controller
{
    static readonly SemaphoreSlim SnapshotGate=new(1,1);
    [HttpGet("/internal/version")]
    public async Task<IActionResult> SnapshotVersion()
    {
        Response.Headers.CacheControl="private, no-store, max-age=0";
        if(!NativeCatalogRuntime.Enabled)
            return StatusCode(501,new { error="SQL snapshot attestation requires SQL authority",productionReady=false });
        if(!await SnapshotGate.WaitAsync(0))
            return StatusCode(429,new { error="A snapshot proof is already running",productionReady=false });
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
        deadline.CancelAfter(TimeSpan.FromSeconds(15));
        try
        {
            var proof=await NativeCatalogRuntime.ReadStore.SnapshotProof(deadline.Token);
            return Json(new { sourceSha=VersionInfo.GitFullSha,catalogBackend="bunny-sql",algorithm=proof.Algorithm,
                snapshotSha256=proof.Sha256,counts=proof.Counts,atomicRead=true,productionReady=false });
        }
        catch(Exception ex) when(ex is NativeSqlException or OperationCanceledException)
        {
            return StatusCode(503,new { error="SQL snapshot unavailable or integrity verification failed",productionReady=false });
        }
        finally { SnapshotGate.Release(); }
    }
}
