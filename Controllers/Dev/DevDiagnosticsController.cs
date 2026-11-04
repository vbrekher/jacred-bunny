using JacRed.Application.Dev;
using Microsoft.AspNetCore.Mvc;
using System;
using System.Threading;
using JacRed.Infrastructure.Persistence.Legacy;
using JacRed.Infrastructure.Persistence.Native;

namespace JacRed.Controllers.Dev
{
    [Route("/dev/[action]")]
    public class DevDiagnosticsController : Controller
    {
        readonly IDevDiagnosticsService _diagnosticsService;

        public DevDiagnosticsController(IDevDiagnosticsService diagnosticsService)
        {
            _diagnosticsService = diagnosticsService;
        }

        public JsonResult FindCorrupt(int sampleSize = 20) => Json(_diagnosticsService.FindCorrupt(sampleSize));

        [HttpGet]
        public JsonResult LegacyExportPreflight()
        {
            Response.Headers.CacheControl = "private, no-store";
            if (NativeCatalogRuntime.Enabled)
                return Json(new LegacyExportPreflightResult("REFUSED", "backend", "SQL_AUTHORITY"));
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(HttpContext.RequestAborted);
            timeout.CancelAfter(TimeSpan.FromSeconds(10));
            return Json(JacRed.Infrastructure.Persistence.Legacy.LegacyExportPreflight.Inspect(
                Environment.GetEnvironmentVariable("JACRED_LEGACY_EXPORT_DATA_ROOT") ?? "/app/Data",
                Environment.GetEnvironmentVariable("JACRED_CONFIG_DIR") ?? "/app/config", timeout.Token));
        }

        public JsonResult FindDuplicateKeys(string tracker = null, bool excludeNumeric = true) =>
            Json(_diagnosticsService.FindDuplicateKeys(tracker, excludeNumeric));

        public JsonResult FindEmptySearchFields(int sampleSize = 20) => Json(_diagnosticsService.FindEmptySearchFields(sampleSize));
    }
}
