using System;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed partial class BunnySqlClient
{
    internal async Task<ReadSession> OpenReadSession(CancellationToken cancellation = default)
    {
        var session = new ReadSession(this);
        try
        {
            await session.Query(new SqlStatement("BEGIN"), cancellation).ConfigureAwait(false);
            return session;
        }
        catch { await session.DisposeAsync().ConfigureAwait(false); throw; }
    }

    // One stream, serialized requests, rotating baton. A failed request poisons
    // the session: never restart a partial snapshot on a new connection.
    internal sealed class ReadSession : IAsyncDisposable
    {
        readonly BunnySqlClient owner;
        readonly SemaphoreSlim gate = new(1, 1);
        Uri destination;
        string baton;
        bool failed, closed;
        internal ReadSession(BunnySqlClient owner) { this.owner = owner; destination = owner.endpoint; }

        internal async Task<SqlResult> Query(SqlStatement statement, CancellationToken cancellation = default)
        {
            await gate.WaitAsync(cancellation).ConfigureAwait(false);
            try
            {
                if (failed || closed) throw new NativeSqlException("SQL snapshot session is unavailable");
                try
                {
                    var payload = await owner.Exchange(new object[] { new { type = "execute", stmt = Statement(statement) } }, destination, baton, cancellation).ConfigureAwait(false);
                    // Save the latest baton before inspecting SQL status so even
                    // rejected statements can release their actual remote stream.
                    if (!payload.TryGetProperty("baton", out var next) || next.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(next.GetString()) || next.GetString().Length > 16384)
                        throw new NativeSqlException("SQL snapshot stream was lost; no partial proof accepted");
                    baton = next.GetString();
                    if (payload.TryGetProperty("base_url", out var location) && location.ValueKind != JsonValueKind.Null)
                    {
                        if (location.ValueKind != JsonValueKind.String) throw new NativeSqlException("Invalid SQL session destination");
                        // Endpoint validates provider domain, scheme, port and
                        // absence of userinfo/path/query before the next token send.
                        if (!Uri.TryCreate(location.GetString(), UriKind.Absolute, out var nextUri) || nextUri.Scheme != "https")
                            throw new NativeSqlException("Invalid SQL session destination");
                        destination = Endpoint(location.GetString());
                    }
                    var results = payload.GetProperty("results");
                    if (results.GetArrayLength() != 1 || results[0].GetProperty("type").GetString() != "ok" || results[0].GetProperty("response").GetProperty("type").GetString() != "execute")
                        throw new NativeSqlException("SQL snapshot query rejected; private payload omitted");
                    return Result(results[0].GetProperty("response").GetProperty("result"));
                }
                catch (Exception ex) when (ex is NativeSqlException or JsonException or InvalidOperationException or System.Collections.Generic.KeyNotFoundException or IndexOutOfRangeException)
                {
                    failed = true;
                    throw new NativeSqlException("SQL snapshot session failed; no retry or partial proof accepted");
                }
            }
            finally { gate.Release(); }
        }

        public async ValueTask DisposeAsync()
        {
            await gate.WaitAsync().ConfigureAwait(false);
            try
            {
                if (closed) return;
                closed = true;
                if (baton == null) return;
                using var cleanup = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                try
                {
                    var response = await owner.Exchange(new object[] { new { type = "close" } }, destination, baton, cleanup.Token).ConfigureAwait(false);
                    var results = response.GetProperty("results");
                    if (results.GetArrayLength() != 1 || results[0].GetProperty("type").GetString() != "ok" || results[0].GetProperty("response").GetProperty("type").GetString() != "close" || (response.TryGetProperty("baton",out var remaining) && remaining.ValueKind != JsonValueKind.Null))
                        throw new NativeSqlException("SQL snapshot stream close was not confirmed");
                }
                catch (Exception ex) when (ex is NativeSqlException or JsonException or InvalidOperationException or System.Collections.Generic.KeyNotFoundException)
                {
                    if (!failed) throw new NativeSqlException("SQL snapshot cleanup not confirmed; proof not accepted");
                }
                finally { baton = null; }
            }
            finally { gate.Release(); }
        }
    }
}
