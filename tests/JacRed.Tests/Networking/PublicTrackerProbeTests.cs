using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using JacRed.Infrastructure.Networking;
using Xunit;

namespace JacRed.Tests.Networking;

public class PublicTrackerProbeTests
{
    [Theory]
    [InlineData("0.0.0.0")][InlineData("10.1.2.3")][InlineData("127.0.0.1")]
    [InlineData("168.63.129.16")][InlineData("100.100.100.200")][InlineData("169.254.169.254")][InlineData("172.31.255.255")]
    [InlineData("192.168.1.1")][InlineData("192.0.0.9")][InlineData("192.0.2.1")]
    [InlineData("198.18.0.1")][InlineData("198.51.100.1")][InlineData("203.0.113.1")]
    [InlineData("224.0.0.1")][InlineData("255.255.255.255")][InlineData("::1")]
    [InlineData("::ffff:127.0.0.1")][InlineData("fc00::1")][InlineData("fe80::1")]
    [InlineData("ff02::1")][InlineData("2001:db8::1")][InlineData("2002:7f00:1::1")]
    [InlineData("2001::1")][InlineData("64:ff9b::7f00:1")][InlineData("3fff::1")]
    public void RejectsLocalReservedAndTransitionAddresses(string address) => Assert.False(PublicTrackerProbe.IsPublic(IPAddress.Parse(address)));

    [Theory]
    [InlineData("1.1.1.1")][InlineData("8.8.8.8")][InlineData("2001:4860:4860::8888")]
    [InlineData("2606:4700:4700::1111")]
    public void AllowsPublicUnicast(string address) => Assert.True(PublicTrackerProbe.IsPublic(IPAddress.Parse(address)));

    [Theory]
    [InlineData("http%3A%2F%2Ftracker.test%2Fannounce%3Fpasskey%3Dsecret")]
    [InlineData("https://user:secret@tracker.test/announce")]
    [InlineData("https://tracker.test/secret/announce")]
    [InlineData("https://tracker.test/announce#secret")]
    [InlineData("https://tracker.test/%2561nnounce")]
    [InlineData("file:///announce")][InlineData("ftp://tracker.test/announce")]
    [InlineData("https://tracker.test/announce%0d%0aHeader:secret")]
    public void NeverPublishesCredentialsOrArbitraryPaths(string candidate) => Assert.False(PublicTrackerProbe.TryNormalize(candidate, out _));

    [Fact]
    public void DecodesPublicAnnounceAndPreservesPathCase()
    {
        Assert.True(PublicTrackerProbe.TryNormalize("udp%3A%2F%2Ftracker.example%3A6969%2Fannounce", out var uri));
        Assert.Equal("udp://tracker.example:6969/announce", uri.AbsoluteUri);
        Assert.False(PublicTrackerProbe.TryNormalize("https://tracker.example/Announce", out _));
        Assert.True(PublicTrackerProbe.TryNormalize("udp://tracker.example/announce", out var defaultPort));
        Assert.Equal(6969, defaultPort.Port);
    }

    [Fact]
    public async Task MixedDnsAnswerRejectsEntireSetWithoutConnecting()
    {
        int resolutions = 0;
        Assert.False(await PublicTrackerProbe.CheckAsync(new Uri("https://tracker.example/announce"), default,
            (host, ct) => { resolutions++; return Task.FromResult(new[] { IPAddress.Parse("8.8.8.8"), IPAddress.Loopback }); }));
        Assert.Equal(1, resolutions);
        Assert.False(PublicTrackerProbe.AllPublic(Array.Empty<IPAddress>()));
    }

    [Fact]
    public async Task LiteralLoopbackNeverReceivesHttpRequest()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);listener.Start();
        try
        {
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            Assert.False(await PublicTrackerProbe.CheckAsync(new Uri($"http://127.0.0.1:{port}/announce"), default));
            Assert.False(listener.Pending());
        }
        finally { listener.Stop(); }
    }

    [Fact]
    public async Task PinnedHttpTransportDoesNotFollowRedirectOrUseProxy()
    {
        var first = new TcpListener(IPAddress.Loopback, 0);
        var target = new TcpListener(IPAddress.Loopback, 0);
        first.Start();target.Start();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        try
        {
            int firstPort = ((IPEndPoint)first.LocalEndpoint).Port;
            int targetPort = ((IPEndPoint)target.LocalEndpoint).Port;
            var uri = new Uri($"http://tracker.invalid:{firstPort}/announce");
            // Exercise the actual transport on loopback directly. The production
            // CheckAsync policy above rejects this address before creating it.
            using var handler = PublicTrackerProbe.CreateHandler(uri, new[] { IPAddress.Loopback });
            Assert.False(handler.UseProxy);
            using var client = new System.Net.Http.HttpClient(handler);
            var request = client.GetAsync(uri, timeout.Token);
            using var connection = await first.AcceptTcpClientAsync(timeout.Token);
            using var stream = connection.GetStream();
            using var reader = new StreamReader(stream, System.Text.Encoding.ASCII, false, 1024, leaveOpen: true);
            Assert.Equal("GET /announce HTTP/1.1", await reader.ReadLineAsync(timeout.Token));
            byte[] redirect = System.Text.Encoding.ASCII.GetBytes($"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{targetPort}/announce\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            await stream.WriteAsync(redirect, timeout.Token);
            using var response = await request;
            Assert.Equal(HttpStatusCode.Found, response.StatusCode);
            Assert.False(target.Pending());
        }
        finally { first.Stop();target.Stop(); }
    }

    [Fact]
    public async Task UdpRequiresActualMatchingBep15Response()
    {
        using var server = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        var probe = PublicTrackerProbe.UdpConnectAsync((IPEndPoint)server.Client.LocalEndPoint, timeout.Token);
        var request = await server.ReceiveAsync(timeout.Token);
        Assert.Equal(16, request.Buffer.Length);
        Assert.Equal(0x41727101980L, BinaryPrimitives.ReadInt64BigEndian(request.Buffer));
        Assert.Equal(0, BinaryPrimitives.ReadInt32BigEndian(request.Buffer.AsSpan(8)));
        Assert.False(probe.IsCompleted); // Sending a datagram is not a successful check.
        byte[] response = new byte[16];
        request.Buffer.AsSpan(12,4).CopyTo(response.AsSpan(4));
        BinaryPrimitives.WriteInt64BigEndian(response.AsSpan(8), 42);
        await server.SendAsync(response, request.RemoteEndPoint, timeout.Token);
        Assert.True(await probe);
        int transaction = BinaryPrimitives.ReadInt32BigEndian(response.AsSpan(4));
        Assert.False(PublicTrackerProbe.ValidConnectResponse(response.AsSpan(0,15), transaction));
        Assert.False(PublicTrackerProbe.ValidConnectResponse(response, transaction ^ 1));
        response[3] = 3; // BEP 15 error action must not count as connect.
        Assert.False(PublicTrackerProbe.ValidConnectResponse(response, transaction));
    }

    [Fact]
    public async Task SilentUdpEndpointTimesOutInsteadOfPassing()
    {
        using var server = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
        using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => PublicTrackerProbe.UdpConnectAsync((IPEndPoint)server.Client.LocalEndPoint, timeout.Token));
    }
}
