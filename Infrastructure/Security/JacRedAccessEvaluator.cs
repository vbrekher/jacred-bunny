using Microsoft.AspNetCore.Http;
using System;

namespace JacRed.Infrastructure.Security
{
    public interface IJacRedAccessEvaluator
    {
        JacRedAccessResult EvaluatePath(string path, HttpContext httpContext);
        bool ShouldSetPrivateNetworkHeader(IClientNetworkContext network, string path);
    }

    public sealed class JacRedAccessEvaluator : IJacRedAccessEvaluator
    {
        readonly IJacRedApiKeyValidator _apiKeyValidator;
        readonly IJacRedDevKeyValidator _devKeyValidator;

        public JacRedAccessEvaluator(IJacRedApiKeyValidator apiKeyValidator, IJacRedDevKeyValidator devKeyValidator)
        {
            _apiKeyValidator = apiKeyValidator;
            _devKeyValidator = devKeyValidator;
        }

        public JacRedAccessResult EvaluatePath(string path, HttpContext httpContext)
        {
            var policy = JacRedEndpointRegistry.ResolvePolicy(path);
            var network = ClientNetworkContext.From(httpContext);
            var method = httpContext.Request.Method;

            if (policy == JacRedAccessPolicy.DevAdmin)
                return EvaluateDevAdmin(network, httpContext, method);

            if (policy == JacRedAccessPolicy.ConfigApi)
                return EvaluateConfigApi(network, httpContext, method);

            if (policy == JacRedAccessPolicy.ApiKeyWhenConfigured && _apiKeyValidator.IsConfigured)
            {
                if (_apiKeyValidator.Validate(httpContext))
                    return JacRedAccessResult.Allow;

                return JacRedAccessResult.Deny(
                    DenyStatus(keyConfigured: true, method),
                    setPrivateNetworkHeader: ShouldSetPrivateNetworkHeader(network, path));
            }

            return JacRedAccessResult.Allow;
        }

        public bool ShouldSetPrivateNetworkHeader(IClientNetworkContext network, string path)
            => network.IsTrustedContext || !JacRedEndpointRegistry.IsRestrictedAdminPath(path);

        JacRedAccessResult EvaluateDevAdmin(IClientNetworkContext network, HttpContext httpContext, string method)
        {
            if (IsDevEndpointAccessAllowed(network, httpContext))
                return JacRedAccessResult.Allow;
            return JacRedAccessResult.Deny(DenyStatus(_devKeyValidator.IsConfigured, method));
        }

        JacRedAccessResult EvaluateConfigApi(IClientNetworkContext network, HttpContext httpContext, string method)
            => EvaluateDevAdmin(network, httpContext, method);

        static bool IsDevEndpointAccessAllowed(IClientNetworkContext network, HttpContext httpContext)
        {
            // A configured administrative key is authoritative on every network.
            // Browser requests to localhost/LAN must not inherit owner privileges.
            if (!string.IsNullOrEmpty(AppInit.conf?.devkey))
                return JacRedKeyUtils.DevKeyMatches(httpContext, AppInit.conf.devkey);
            return IsTrustedLanClient(network, httpContext) && IsTrustedBrowserContext(httpContext.Request);
        }

        static bool IsTrustedBrowserContext(HttpRequest request)
        {
            string site=request.Headers["Sec-Fetch-Site"].ToString();
            if(site.Length>0 && !site.Equals("same-origin",StringComparison.OrdinalIgnoreCase) && !site.Equals("none",StringComparison.OrdinalIgnoreCase))
                return false;
            string origin=request.Headers.Origin.ToString();
            if(origin.Length>0 && !MatchesOrigin(origin,request,originOnly:true))return false;
            string referer=request.Headers.Referer.ToString();
            return referer.Length==0 || MatchesOrigin(referer,request,originOnly:false);
        }

        static bool MatchesOrigin(string value,HttpRequest request,bool originOnly)
        {
            if(!Uri.TryCreate(value,UriKind.Absolute,out var uri) ||
                uri.Scheme is not ("http" or "https") || !string.IsNullOrEmpty(uri.UserInfo) ||
                (originOnly && (uri.AbsolutePath!="/" || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))))
                return false;
            if(!Uri.TryCreate(request.Scheme+"://"+request.Host.ToUriComponent()+"/",UriKind.Absolute,out var target) || !string.IsNullOrEmpty(target.UserInfo))
                return false;
            return uri.Scheme.Equals(target.Scheme,StringComparison.OrdinalIgnoreCase) &&
                uri.IdnHost.Equals(target.IdnHost,StringComparison.OrdinalIgnoreCase) && uri.Port==target.Port;
        }

        /// <summary>
        /// LAN / direct localhost. Reverse proxy (loopback or Docker/LAN peer) alone is not enough.
        /// </summary>
        static bool IsTrustedLanClient(IClientNetworkContext network, HttpContext httpContext)
        {
            if (!network.IsDirectLocalClient)
                return false;

            // Reverse proxy on loopback OR Docker/LAN (e.g. Traefik peer 172.x): ClientIp stays the
            // proxy private IP and looks like LAN. Proxy identity headers mean the real client is
            // behind the proxy — require DEV key. Do not trust header values for granting LAN access.
            if (network.IsViaLocalPeer && ClientNetworkContext.HasProxyClientIdentityHeaders(httpContext))
                return false;

            return true;
        }

        public static int DenyStatus(bool keyConfigured, string method)
            => method == "OPTIONS" ? 204 : (keyConfigured ? 401 : 403);
    }
}
