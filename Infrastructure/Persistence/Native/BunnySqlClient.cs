using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed record SqlStatement(string Sql, params object[] Args);
internal sealed record SqlResult(IReadOnlyList<IReadOnlyDictionary<string, object>> Rows, long Affected);
internal sealed class NativeSqlException : Exception
{
    public NativeSqlException(string message) : base(message) { }
}

/// <summary>Bounded Hrana HTTP transport. Mutations are never retried automatically.</summary>
internal sealed class BunnySqlClient : IDisposable
{
    const int MaxRequest = 4 * 1024 * 1024;
    const int MaxResponse = 8 * 1024 * 1024;
    readonly HttpClient http;
    readonly Uri endpoint;
    readonly string token;

    public BunnySqlClient(string databaseUrl, string token, HttpMessageHandler handler = null)
    {
        endpoint = Endpoint(databaseUrl);
        if (string.IsNullOrWhiteSpace(token) || token.Any(char.IsControl))
            throw new NativeSqlException("Database token missing or invalid");
        this.token = token;
        http = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }, disposeHandler: true)
        { Timeout = TimeSpan.FromSeconds(30) };
    }

    internal static Uri Endpoint(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "https" && uri.Scheme != "libsql") ||
            !uri.Host.EndsWith(".bunnydb.net", StringComparison.OrdinalIgnoreCase) ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) ||
            !string.IsNullOrEmpty(uri.Fragment) || (uri.AbsolutePath != "/" && uri.AbsolutePath != "") ||
            (!uri.IsDefaultPort && uri.Port != -1))
            throw new NativeSqlException("Unexpected database endpoint");
        return new Uri("https://" + uri.Host + "/v2/pipeline");
    }

    internal static object Encode(object value) => value switch
    {
        null => new { type = "null" },
        string text => new { type = "text", value = text },
        bool boolean => new { type = "integer", value = boolean ? "1" : "0" },
        byte or sbyte or short or ushort or int or uint or long =>
            new { type = "integer", value = Convert.ToString(value, CultureInfo.InvariantCulture) },
        double number when double.IsFinite(number) => new { type = "float", value = number },
        float number when float.IsFinite(number) => new { type = "float", value = (double)number },
        byte[] bytes => new { type = "blob", base64 = Convert.ToBase64String(bytes) },
        _ => throw new NativeSqlException("Unsupported SQL parameter type or range")
    };

    internal static object Decode(JsonElement value)
    {
        try
        {
            switch (value.GetProperty("type").GetString())
            {
                case "null": return null;
                case "text": return value.GetProperty("value").GetString();
                case "integer": return long.Parse(value.GetProperty("value").GetString(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
                case "float":
                    var number = value.GetProperty("value").GetDouble();
                    if (!double.IsFinite(number)) break;
                    return number;
                case "blob":
                    var encoded = value.GetProperty("base64").GetString();
                    return Convert.FromBase64String(encoded.PadRight((encoded.Length + 3) / 4 * 4, '='));
            }
        }
        catch (Exception ex) when (ex is FormatException or OverflowException or InvalidOperationException or KeyNotFoundException or ArgumentException) { }
        throw new NativeSqlException("Invalid SQL result value; payload omitted");
    }

    static object Statement(SqlStatement statement) => new
    {
        sql = statement.Sql,
        args = statement.Args.Select(Encode).ToArray(),
        named_args = Array.Empty<object>(),
        want_rows = true
    };

    async Task<JsonElement> Send(object request, CancellationToken cancellation)
    {
        byte[] body;
        try { body = JsonSerializer.SerializeToUtf8Bytes(new { requests = new[] { request, new { type = "close" } } }); }
        catch (Exception ex) when (ex is ArgumentException or JsonException) { throw new NativeSqlException("Cannot encode bounded SQL request"); }
        if (body.Length > MaxRequest) throw new NativeSqlException("SQL request exceeds bounded batch size");
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromSeconds(30));
        cancellation = deadline.Token;
        using var message = new HttpRequestMessage(HttpMethod.Post, endpoint);
        message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        message.Content = new ByteArrayContent(body);
        message.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        try
        {
            using var response = await http.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, cancellation).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode) throw new NativeSqlException("SQL HTTP " + (int)response.StatusCode + "; no retry attempted");
            using var stream = await response.Content.ReadAsStreamAsync(cancellation).ConfigureAwait(false);
            using var buffer = new MemoryStream();
            var block = new byte[65536];
            int read;
            while ((read = await stream.ReadAsync(block, cancellation).ConfigureAwait(false)) != 0)
            {
                if (buffer.Length + read > MaxResponse) throw new NativeSqlException("SQL response exceeds bounded size");
                buffer.Write(block, 0, read);
            }
            using var document = JsonDocument.Parse(buffer.ToArray(), new JsonDocumentOptions { MaxDepth = 128 });
            var results = document.RootElement.GetProperty("results");
            if (results.GetArrayLength() != 2 || results[0].GetProperty("type").GetString() != "ok" || results[1].GetProperty("type").GetString() != "ok")
                throw new NativeSqlException("SQL operation rejected; private payload omitted");
            return results[0].GetProperty("response").GetProperty("result").Clone();
        }
        catch (OperationCanceledException) { throw new NativeSqlException("SQL request cancelled or timed out; outcome may be uncertain, no retry attempted"); }
        catch (HttpRequestException) { throw new NativeSqlException("SQL network failure; outcome may be uncertain, no retry attempted"); }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException or IOException)
        { throw new NativeSqlException("SQL response unavailable or malformed; no retry attempted"); }
    }

    static SqlResult Result(JsonElement result)
    {
        try
        {
            var columns = result.GetProperty("cols").EnumerateArray().Select(x => x.GetProperty("name").GetString()).ToArray();
            var rows = new List<IReadOnlyDictionary<string, object>>();
            foreach (var row in result.GetProperty("rows").EnumerateArray())
            {
                if (row.GetArrayLength() != columns.Length) throw new NativeSqlException("SQL result shape mismatch");
                var record = new Dictionary<string, object>(StringComparer.Ordinal);
                for (int i = 0; i < columns.Length; i++) record.Add(columns[i], Decode(row[i]));
                rows.Add(record);
            }
            return new SqlResult(rows, result.TryGetProperty("affected_row_count", out var affected) ? affected.GetInt64() : 0);
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException or ArgumentException or FormatException)
        { throw new NativeSqlException("SQL result shape invalid; payload omitted"); }
    }

    public async Task<SqlResult> Query(SqlStatement statement, CancellationToken cancellation = default)
        => Result(await Send(new { type = "execute", stmt = Statement(statement) }, cancellation).ConfigureAwait(false));

    /// <summary>Every step depends on prior success. Failed BEGIN cannot permit autocommit writes.</summary>
    public async Task<IReadOnlyList<SqlResult>> Transaction(IReadOnlyList<SqlStatement> statements, bool write = true, CancellationToken cancellation = default)
    {
        if (statements.Count == 0 || statements.Count > 1000) throw new NativeSqlException("Invalid transaction batch size");
        var all = new[] { new SqlStatement(write ? "BEGIN IMMEDIATE" : "BEGIN") }.Concat(statements).Append(new SqlStatement("COMMIT")).ToArray();
        var steps = new List<object>();
        for (int i = 0; i < all.Length; i++)
            steps.Add(new { condition = i == 0 ? null : (object)new { type = "ok", step = i - 1 }, stmt = Statement(all[i]) });
        steps.Add(new { condition = new { type = "not", cond = new { type = "ok", step = all.Length - 1 } }, stmt = Statement(new SqlStatement("ROLLBACK")) });
        var response = await Send(new { type = "batch", batch = new { steps } }, cancellation).ConfigureAwait(false);
        try
        {
            var results = response.GetProperty("step_results");
            var errors = response.GetProperty("step_errors");
            if (results.GetArrayLength() != steps.Count || errors.GetArrayLength() != steps.Count)
                throw new NativeSqlException("Transaction result incomplete; outcome not accepted");
            for (int i = 0; i < all.Length; i++)
                if (errors[i].ValueKind != JsonValueKind.Null || results[i].ValueKind == JsonValueKind.Null)
                    throw new NativeSqlException("SQL transaction failed or skipped; no retry attempted");
            return Enumerable.Range(1, statements.Count).Select(i => Result(results[i])).ToArray();
        }
        catch (Exception ex) when (ex is KeyNotFoundException or InvalidOperationException or IndexOutOfRangeException)
        { throw new NativeSqlException("Malformed transaction result; outcome not accepted"); }
    }

    public void Dispose() => http.Dispose();
}
