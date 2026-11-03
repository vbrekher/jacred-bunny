using System;
using System.Threading;
using System.Threading.Tasks;
using JacRed.Infrastructure.Persistence.Native;
using Microsoft.AspNetCore.Mvc;

namespace JacRed.Controllers;

public class InternalVersionController : Controller
{
    [HttpGet("/internal/version")]
    public async Task<IActionResult> SnapshotVersion()
    {
        Response.Headers.CacheControl="private, no-store, max-age=0";
        if(!NativeCatalogRuntime.Enabled)
            return StatusCode(501,new { error="SQL snapshot attestation requires SQL authority",productionReady=false });
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
        deadline.CancelAfter(TimeSpan.FromSeconds(15));
        try
        {
            var proof=await NativeCatalogRuntime.ReadStore.SnapshotProof(deadline.Token);
            return Json(new { sourceSha=VersionInfo.GitFullSha,catalogBackend="bunny-sql",algorithm=proof.Algorithm,
                snapshotSha256=proof.Sha256,counts=proof.Counts,atomicRead=true,productionReady=false });
        }
        catch(NativeSqlException)
        {
            return StatusCode(503,new { error="SQL snapshot unavailable or integrity verification failed",productionReady=false });
        }
    }
}
