using System.Text.Json;
using JacRed.Controllers;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Xunit;

namespace JacRed.Tests.Controllers;

public class HealthControllerConfTests
{
    [Fact]
    public void Version_exposes_corresponding_fork_commit_source()
    {
        var result = Assert.IsType<JsonResult>(new HealthController().Version());
        var json = JsonSerializer.SerializeToElement(result.Value);
        Assert.Equal(VersionInfo.GitFullSha, json.GetProperty("gitFullSha").GetString());
        Assert.Equal("filedb", json.GetProperty("catalogBackend").GetString());
        Assert.Equal(VersionInfo.SourceUrl, json.GetProperty("sourceUrl").GetString());
        Assert.Matches("^[a-f0-9]{40}$", VersionInfo.GitFullSha);
        Assert.Equal("https://github.com/vbrekher/jacred-bunny/tree/" + VersionInfo.GitFullSha, VersionInfo.SourceUrl);
    }

    [Fact]
    public void Conf_without_server_apikey_returns_open_access()
    {
        WithApiKey(null, () =>
        {
            var json = InvokeConf(apikey: null);

            Assert.True(json.GetProperty("jacred").GetBoolean());
            Assert.False(json.GetProperty("configured").GetBoolean());
            Assert.True(json.GetProperty("apikey").GetBoolean());
            Assert.Equal(VersionInfo.Version, json.GetProperty("version").GetString());
        });
    }

    [Fact]
    public void Conf_with_matching_apikey_returns_valid()
    {
        WithApiKey("secret-key", () =>
        {
            var json = InvokeConf(apikey: "secret-key");

            Assert.True(json.GetProperty("jacred").GetBoolean());
            Assert.True(json.GetProperty("configured").GetBoolean());
            Assert.True(json.GetProperty("apikey").GetBoolean());
            Assert.Equal(VersionInfo.Version, json.GetProperty("version").GetString());
        });
    }

    [Fact]
    public void Conf_with_missing_apikey_when_configured_returns_invalid()
    {
        WithApiKey("secret-key", () =>
        {
            var json = InvokeConf(apikey: null);

            Assert.True(json.GetProperty("jacred").GetBoolean());
            Assert.True(json.GetProperty("configured").GetBoolean());
            Assert.False(json.GetProperty("apikey").GetBoolean());
            Assert.Equal(VersionInfo.Version, json.GetProperty("version").GetString());
        });
    }

    [Fact]
    public void Conf_with_wrong_apikey_when_configured_returns_invalid()
    {
        WithApiKey("secret-key", () =>
        {
            var json = InvokeConf(apikey: "wrong");

            Assert.True(json.GetProperty("jacred").GetBoolean());
            Assert.True(json.GetProperty("configured").GetBoolean());
            Assert.False(json.GetProperty("apikey").GetBoolean());
            Assert.Equal(VersionInfo.Version, json.GetProperty("version").GetString());
        });
    }

    static JsonElement InvokeConf(string apikey)
    {
        var controller = new HealthController
        {
            ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext()
            }
        };

        var result = controller.JacRedConf(apikey);
        Assert.NotNull(result.Value);

        var serialized = JsonSerializer.Serialize(result.Value);
        return JsonSerializer.Deserialize<JsonElement>(serialized);
    }

    static void WithApiKey(string value, Action action)
    {
        _ = AppInit.conf;
        var previous = AppInit.conf.apikey;
        try
        {
            AppInit.conf.apikey = value;
            action();
        }
        finally
        {
            AppInit.conf.apikey = previous;
        }
    }
}
