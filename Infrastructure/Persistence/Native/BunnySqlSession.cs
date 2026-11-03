using System;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed partial class BunnySqlClient
{
    internal async Task<SqlSession> OpenReadSession(CancellationToken cancellation = default)
    {
        var session = new SqlSession(this);
        try
        {
            await session.Query(new SqlStatement("BEGIN"), cancellation).ConfigureAwait(false);
            return session;
        }
        catch { await session.DisposeAsync().ConfigureAwait(false); throw; }
    }

    internal async Task<SqlSession> OpenWriteSession(CancellationToken cancellation = default)
    {
        var session = new SqlSession(this, write: true);
        try
        {
            await session.Query(new SqlStatement("BEGIN IMMEDIATE"), cancellation).ConfigureAwait(false);
            return session;
        }
        catch { await session.DisposeAsync().ConfigureAwait(false); throw; }
    }

    // One stream, serialized requests, rotating baton. A failed request poisons
    // the session: never restart a partial snapshot on a new connection.
    internal sealed class SqlSession : IAsyncDisposable
    {
        readonly BunnySqlClient owner;
        readonly SemaphoreSlim gate = new(1, 1);
        Uri destination;
        string baton;
        bool failed, closed, committed;
        readonly bool write;
        internal SqlSession(BunnySqlClient owner, bool write=false) { this.owner = owner; this.write=write; destination = owner.endpoint; }

        internal async Task<SqlResult> Query(SqlStatement statement, CancellationToken cancellation = default)
            => await Execute(new { type = "execute", stmt = Statement(statement) }, "execute", Result, cancellation).ConfigureAwait(false);

        internal async Task Batch(System.Collections.Generic.IReadOnlyList<SqlStatement> statements, CancellationToken cancellation)
        {
            if(!write || statements.Count==0 || statements.Count>512)throw new NativeSqlException("Invalid write page");
            var steps=new System.Collections.Generic.List<object>();
            for(int i=0;i<statements.Count;i++)
                steps.Add(new { condition=i==0?null:(object)new { type="ok",step=i-1 },stmt=Statement(statements[i]) });
            await Execute(new { type="batch",batch=new { steps } },"batch",response=>
            {
                var results=response.GetProperty("step_results");var errors=response.GetProperty("step_errors");
                if(results.GetArrayLength()!=statements.Count || errors.GetArrayLength()!=statements.Count)
                    throw new NativeSqlException("Write page response incomplete");
                for(int i=0;i<statements.Count;i++)
                    if(results[i].ValueKind==JsonValueKind.Null || errors[i].ValueKind!=JsonValueKind.Null)
                        throw new NativeSqlException("Write page failed or skipped");
                return true;
            },cancellation).ConfigureAwait(false);
        }

        internal async Task Commit(CancellationToken cancellation)
        {
            if(!write)throw new NativeSqlException("Read session cannot commit writes");
            await Execute(new { type="execute",stmt=Statement(new SqlStatement("COMMIT")) },"execute",Result,cancellation,committing:true).ConfigureAwait(false);
        }

        async Task<T> Execute<T>(object request,string responseType,Func<JsonElement,T> decode,CancellationToken cancellation,bool committing=false)
        {
            await gate.WaitAsync(cancellation).ConfigureAwait(false);
            try
            {
                if (failed || closed || committed) throw new NativeSqlException("SQL session is unavailable");
                try
                {
                    var payload = await owner.Exchange(new object[] { request }, destination, baton, cancellation).ConfigureAwait(false);
                    // Save the latest baton before inspecting SQL status so even
                    // rejected statements can release their actual remote stream.
                    if (!payload.TryGetProperty("baton", out var next) || next.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(next.GetString()) || next.GetString().Length > 16384)
                        throw new NativeSqlException("SQL stream was lost; no partial result accepted");
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
                    if (results.GetArrayLength() != 1 || results[0].GetProperty("type").GetString() != "ok" || results[0].GetProperty("response").GetProperty("type").GetString() != responseType)
                        throw new NativeSqlException("SQL query rejected; private payload omitted");
                    var result=decode(results[0].GetProperty("response").GetProperty("result"));
                    if(committing)committed=true;
                    return result;
                }
                catch (Exception ex) when (ex is NativeSqlException or JsonException or InvalidOperationException or System.Collections.Generic.KeyNotFoundException or IndexOutOfRangeException)
                {
                    failed = true;
                    throw new NativeSqlException("SQL session failed; outcome may be uncertain; no automatic replay");
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
                    var rollback=write&&!committed;
                    object[] requests=rollback?new object[] { new { type="execute",stmt=Statement(new SqlStatement("ROLLBACK")) },new { type="close" } }:new object[] { new { type="close" } };
                    var response = await owner.Exchange(requests, destination, baton, cleanup.Token).ConfigureAwait(false);
                    var results = response.GetProperty("results");
                    if (results.GetArrayLength() != requests.Length || results[requests.Length-1].GetProperty("type").GetString() != "ok" || results[requests.Length-1].GetProperty("response").GetProperty("type").GetString() != "close" || (response.TryGetProperty("baton",out var remaining) && remaining.ValueKind != JsonValueKind.Null))
                        throw new NativeSqlException("SQL stream close was not confirmed");
                }
                catch (Exception ex) when (ex is NativeSqlException or JsonException or InvalidOperationException or System.Collections.Generic.KeyNotFoundException)
                {
                    if (!failed) throw new NativeSqlException("SQL stream cleanup not confirmed; proof not accepted");
                }
                finally { baton = null; }
            }
            finally { gate.Release(); }
        }
    }
}
