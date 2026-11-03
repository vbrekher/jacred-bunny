using System.Net;
using JacRed.Infrastructure.Security;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace JacRed.Tests.Security;

public class JacRedAccessEvaluatorTests
{
    const string ConfigPath = "/api/v1.0/config";
    const string DevPath = "/dev/";

    [Fact]
    public void Spoofed_CF_Connecting_IP_from_public_peer_is_denied_without_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("203.0.113.10"),
                headers: ("CF-Connecting-IP", "127.0.0.1"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Loopback_peer_with_public_CF_IP_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Loopback,
                headers: ("CF-Connecting-IP", "8.8.8.8"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Direct_loopback_without_headers_is_allowed()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(peerIp: IPAddress.Loopback);

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
            Assert.True(evaluator.EvaluatePath(DevPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Direct_LAN_without_headers_is_allowed()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(peerIp: IPAddress.Parse("10.0.0.5"));

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Empty_devkey_denies_public_peer()
    {
        WithDevKey("", () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(peerIp: IPAddress.Parse("203.0.113.10"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Valid_X_Dev_Key_allows_public_peer()
    {
        WithDevKey("secret-dev-key", () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("203.0.113.10"),
                headers: ("X-Dev-Key", "secret-dev-key"));

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
            Assert.True(evaluator.EvaluatePath(DevPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Invalid_X_Dev_Key_denies_public_peer_with_401()
    {
        WithDevKey("secret-dev-key", () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("203.0.113.10"),
                headers: ("X-Dev-Key", "wrong"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(401, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Loopback_proxy_with_valid_devkey_allows_public_client()
    {
        WithDevKey("secret-dev-key", () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Loopback,
                ("CF-Connecting-IP", "8.8.8.8"),
                ("X-Dev-Key", "secret-dev-key"));

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_XFF_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                headers: ("X-Forwarded-For", "203.0.113.50"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_X_Real_IP_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                headers: ("X-Real-IP", "203.0.113.50"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_XFF_and_valid_devkey_allows()
    {
        WithDevKey("secret-dev-key", () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                ("X-Forwarded-For", "203.0.113.50"),
                ("X-Dev-Key", "secret-dev-key"));

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
            Assert.True(evaluator.EvaluatePath(DevPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Docker_bridge_peer_without_proxy_headers_is_allowed()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(peerIp: IPAddress.Parse("172.18.0.2"));

            Assert.True(evaluator.EvaluatePath(ConfigPath, ctx).IsAllowed);
            Assert.True(evaluator.EvaluatePath(DevPath, ctx).IsAllowed);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_X_Forwarded_Host_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                headers: ("X-Forwarded-Host", "jacred.example.com"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_Forwarded_header_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                headers: ("Forwarded", "for=203.0.113.50;proto=https"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Fact]
    public void Docker_proxy_peer_with_X_Forwarded_Proto_requires_devkey()
    {
        WithDevKey(null, () =>
        {
            var evaluator = CreateEvaluator();
            var ctx = CreateContext(
                peerIp: IPAddress.Parse("172.18.0.2"),
                headers: ("X-Forwarded-Proto", "http"));

            var result = evaluator.EvaluatePath(ConfigPath, ctx);

            Assert.False(result.IsAllowed);
            Assert.Equal(403, result.DenyStatusCode);
        });
    }

    [Theory]
    [InlineData("127.0.0.1")][InlineData("10.0.0.5")][InlineData("::1")]
    public void Configured_devkey_cannot_be_bypassed_by_direct_local_client(string address)
    {
        WithDevKey("secret-dev-key",()=>
        {
            var evaluator=CreateEvaluator();var context=CreateContext(IPAddress.Parse(address));
            foreach(var path in new[]{ConfigPath,DevPath,"/internal/version","/cron/subsplease/parse"})
            {
                var result=evaluator.EvaluatePath(path,context);Assert.False(result.IsAllowed);Assert.Equal(401,result.DenyStatusCode);
            }
            context.Request.Headers["X-Dev-Key"]="wrong";
            Assert.False(evaluator.EvaluatePath(ConfigPath,context).IsAllowed);
            context.Request.Headers["X-Dev-Key"]="secret-dev-key";
            context.Request.Headers.Origin="https://foreign.invalid";
            Assert.True(evaluator.EvaluatePath(ConfigPath,context).IsAllowed);
        });
    }

    [Theory]
    [InlineData("Origin","https://foreign.invalid")]
    [InlineData("Origin","null")]
    [InlineData("Origin","http://localhost:9118")]
    [InlineData("Origin","http://user@localhost:9117")]
    [InlineData("Origin","http://localhost:9117/path")]
    [InlineData("Sec-Fetch-Site","cross-site")]
    [InlineData("Sec-Fetch-Site","same-site")]
    [InlineData("Sec-Fetch-Site","unknown")]
    [InlineData("Referer","https://foreign.invalid/page")]
    public void Keyless_LAN_mode_rejects_foreign_or_opaque_browser_context(string header,string value)
    {
        WithDevKey(null,()=>
        {
            var context=CreateContext(IPAddress.Loopback,(header,value));
            context.Request.Scheme="http";context.Request.Host=new HostString("localhost",9117);
            var result=CreateEvaluator().EvaluatePath(ConfigPath,context);
            Assert.False(result.IsAllowed);Assert.Equal(403,result.DenyStatusCode);
        });
    }

    [Fact]
    public void Keyless_LAN_same_origin_browser_keeps_legacy_access()
    {
        WithDevKey(null,()=>
        {
            var context=CreateContext(IPAddress.Loopback,("Origin","http://localhost:9117"),("Referer","http://localhost:9117/settings"),("Sec-Fetch-Site","same-origin"));
            context.Request.Scheme="http";context.Request.Host=new HostString("localhost",9117);
            Assert.True(CreateEvaluator().EvaluatePath(ConfigPath,context).IsAllowed);
        });
    }

    [Theory]
    [InlineData("http","localhost:9117","http://LOCALHOST:9117")]
    [InlineData("https","localhost","https://localhost:443")]
    [InlineData("http","[::1]:9117","http://[::1]:9117")]
    [InlineData("https","xn--e1afmkfd.xn--p1ai","https://пример.рф")]
    public void Keyless_same_origin_matching_handles_default_ports_IPv6_and_IDN(string scheme,string host,string origin)
    {
        WithDevKey(null,()=>
        {
            var context=CreateContext(IPAddress.Loopback,("Origin",origin));context.Request.Scheme=scheme;context.Request.Host=new HostString(host);
            Assert.True(CreateEvaluator().EvaluatePath(ConfigPath,context).IsAllowed);
        });
    }

    static JacRedAccessEvaluator CreateEvaluator()
        => new(new JacRedApiKeyValidator(), new JacRedDevKeyValidator());

    static DefaultHttpContext CreateContext(IPAddress peerIp, params (string Name, string Value)[] headers)
    {
        var ctx = new DefaultHttpContext();
        ctx.Connection.RemoteIpAddress = peerIp;
        ctx.Request.Method = "GET";
        foreach (var (name, value) in headers)
            ctx.Request.Headers[name] = value;
        ClientNetworkContext.CaptureOriginalRemoteIp(ctx);
        return ctx;
    }

    static void WithDevKey(string value, Action action)
    {
        _ = AppInit.conf; // ensure provider initialized
        var previous = AppInit.conf.devkey;
        try
        {
            AppInit.conf.devkey = value;
            action();
        }
        finally
        {
            AppInit.conf.devkey = previous;
        }
    }
}
