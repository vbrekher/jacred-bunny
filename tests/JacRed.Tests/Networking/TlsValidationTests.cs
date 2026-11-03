using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Xunit;
using JacHttp = JacRed.Infrastructure.Networking.HttpClient;

namespace JacRed.Tests.Networking;

public class TlsValidationTests
{
    [Fact]
    public async Task Shared_tracker_transport_rejects_an_untrusted_certificate()
    {
        using var rsa=RSA.Create(2048);
        var request=new CertificateRequest("CN=localhost",rsa,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
        var names=new SubjectAlternativeNameBuilder();names.AddIpAddress(IPAddress.Loopback);
        request.CertificateExtensions.Add(names.Build());
        using var certificate=request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1),DateTimeOffset.UtcNow.AddHours(1));
        using var deadline=new CancellationTokenSource(TimeSpan.FromSeconds(8));
        using var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();
        var port=((IPEndPoint)listener.LocalEndpoint).Port;
        var server=Task.Run(async()=>
        {
            try
            {
                using var tcp=await listener.AcceptTcpClientAsync(deadline.Token);
                using var tls=new SslStream(tcp.GetStream(),false);
                await tls.AuthenticateAsServerAsync(new SslServerAuthenticationOptions { ServerCertificate=certificate },deadline.Token);
                var buffer=new byte[4096];
                if(await tls.ReadAsync(buffer,deadline.Token)>0)
                    await tls.WriteAsync(Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"),deadline.Token);
            }
            catch(Exception error) when(error is AuthenticationException or IOException or OperationCanceledException) { }
        });
        try
        {
            using var handler=JacHttp.CreateHandler(null,DecompressionMethods.None,allowAutoRedirect:false);
            handler.UseProxy=false;
            using var client=new System.Net.Http.HttpClient(handler) { Timeout=TimeSpan.FromSeconds(5) };
            await Assert.ThrowsAsync<HttpRequestException>(()=>client.GetAsync("https://127.0.0.1:"+port,deadline.Token));
        }
        finally
        {
            await deadline.CancelAsync();listener.Stop();await server;
        }
    }
}
