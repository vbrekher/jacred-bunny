using System;
using System.Buffers.Binary;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Web;

namespace JacRed.Infrastructure.Networking;

// Magnet-provided endpoints are untrusted. Resolve once, reject the entire DNS
// answer set if any address is non-public, and connect only to a vetted IP.
internal static class PublicTrackerProbe
{
    internal static bool TryNormalize(string encoded, out Uri uri)
    {
        uri = null;
        if (string.IsNullOrEmpty(encoded) || encoded.Length > 2048) return false;
        string value = HttpUtility.UrlDecode(encoded);
        if (value.Any(char.IsControl) || value.Contains('%') || value.Contains('\\') ||
            !Uri.TryCreate(value, UriKind.Absolute, out var parsed) ||
            parsed.Scheme is not ("http" or "https" or "udp") ||
            !string.IsNullOrEmpty(parsed.UserInfo) || !string.IsNullOrEmpty(parsed.Query) ||
            !string.IsNullOrEmpty(parsed.Fragment) || parsed.HostNameType == UriHostNameType.Unknown ||
            (parsed.Port != -1 && parsed.Port < 1) || parsed.Port > 65535) return false;
        // No arbitrary/private passkey paths in the public tracker list.
        if (parsed.AbsolutePath is not ("/" or "/announce" or "/announce.php")) return false;
        if (parsed.Scheme == "udp" && parsed.Port == -1) parsed = new UriBuilder(parsed) { Port = 6969 }.Uri;
        uri = parsed;
        return true;
    }

    internal static bool IsPublic(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
        byte[] b = address.GetAddressBytes();
        if (b.Length == 4)
        {
            if (b[0] is 0 or 10 or 127 || b[0] >= 224 ||
                (b[0] == 100 && b[1] is >= 64 and <= 127) ||
                (b[0] == 168 && b[1] == 63 && b[2] == 129 && b[3] == 16) ||
                (b[0] == 169 && b[1] == 254) || (b[0] == 172 && b[1] is >= 16 and <= 31) ||
                (b[0] == 192 && (b[1] == 168 || (b[1] == 0 && b[2] is 0 or 2) || (b[1] == 88 && b[2] == 99))) ||
                (b[0] == 198 && (b[1] is 18 or 19 || (b[1] == 51 && b[2] == 100))) ||
                (b[0] == 203 && b[1] == 0 && b[2] == 113)) return false;
            return true;
        }
        // Only native global unicast; reject scope identifiers and transition,
        // documentation, protocol-assignment and all local/reserved space.
        return b.Length == 16 && address.ScopeId == 0 && (b[0] & 0xe0) == 0x20 &&
            !(b[0] == 0x20 && b[1] == 0x02) &&
            !(b[0] == 0x20 && b[1] == 0x01 && ((b[2] == 0x0d && b[3] == 0xb8) || b[2] < 2)) &&
            !(b[0] == 0x3f && b[1] == 0xff && (b[2] & 0xf0) == 0);
    }

    internal static bool AllPublic(IPAddress[] addresses) => addresses.Length > 0 && addresses.All(IsPublic);

    internal static async Task<bool> CheckAsync(Uri uri, CancellationToken cancellationToken,
        Func<string, CancellationToken, Task<IPAddress[]>> resolve = null)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromSeconds(7));
        var ct = deadline.Token;
        try
        {
            IPAddress[] addresses = IPAddress.TryParse(uri.DnsSafeHost, out var literal)
                ? new[] { literal }
                : await (resolve ?? ((host, token) => Dns.GetHostAddressesAsync(host, token)))(uri.DnsSafeHost, ct);
            if (!AllPublic(addresses)) return false;
            if (uri.Scheme == "udp") return await UdpConnectAsync(new IPEndPoint(addresses[0], uri.Port), ct);
            using var handler = CreateHandler(uri, addresses);
            using var client = new System.Net.Http.HttpClient(handler);
            using var response = await client.GetAsync(uri, HttpCompletionOption.ResponseHeadersRead, ct);
            // No redirect following or authentication challenge can count as a
            // verified public endpoint. Tracker failures for missing announce
            // parameters commonly use HTTP 400 and still demonstrate reachability.
            return response.IsSuccessStatusCode || response.StatusCode == HttpStatusCode.BadRequest;
        }
        catch (Exception ex) when (ex is HttpRequestException or SocketException or OperationCanceledException or IOException) { return false; }
    }

    internal static SocketsHttpHandler CreateHandler(Uri uri, IPAddress[] addresses)
    {
        return new SocketsHttpHandler
            {
                UseProxy = false, AllowAutoRedirect = false, UseCookies = false,
                ConnectTimeout = TimeSpan.FromSeconds(7), MaxResponseHeadersLength = 16,
                ConnectCallback = async (context, token) =>
                {
                    if (!context.DnsEndPoint.Host.Equals(uri.DnsSafeHost, StringComparison.OrdinalIgnoreCase) || context.DnsEndPoint.Port != uri.Port)
                        throw new HttpRequestException("Tracker destination changed");
                    var socket = new Socket(addresses[0].AddressFamily, SocketType.Stream, ProtocolType.Tcp);
                    try
                    {
                        await socket.ConnectAsync(new IPEndPoint(addresses[0], uri.Port), token);
                        return new NetworkStream(socket, ownsSocket: true);
                    }
                    catch { socket.Dispose(); throw; }
                }
        };
    }

    internal static byte[] ConnectRequest(int transaction)
    {
        byte[] packet = new byte[16];
        BinaryPrimitives.WriteInt64BigEndian(packet, 0x41727101980);
        BinaryPrimitives.WriteInt32BigEndian(packet.AsSpan(12), transaction);
        return packet;
    }

    internal static bool ValidConnectResponse(ReadOnlySpan<byte> packet, int transaction) =>
        packet.Length >= 16 && BinaryPrimitives.ReadInt32BigEndian(packet) == 0 &&
        BinaryPrimitives.ReadInt32BigEndian(packet.Slice(4)) == transaction;

    // BEP 15 connection handshake only: no fabricated announce, swarm or peer ID.
    internal static async Task<bool> UdpConnectAsync(IPEndPoint endpoint, CancellationToken ct)
    {
        using var client = new UdpClient(endpoint.AddressFamily);
        client.Connect(endpoint);
        int transaction = BinaryPrimitives.ReadInt32BigEndian(RandomNumberGenerator.GetBytes(4));
        await client.SendAsync(ConnectRequest(transaction), ct);
        var response = await client.ReceiveAsync(ct);
        return ValidConnectResponse(response.Buffer, transaction);
    }
}
