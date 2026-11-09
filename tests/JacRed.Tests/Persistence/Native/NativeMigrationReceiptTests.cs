using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace JacRed.Tests.Persistence.Native;

public sealed class NativeMigrationReceiptTests
{
    [Fact]
    public void Shared_migration_vectors_match_runtime_Newtonsoft_and_ordinal_receipts()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "NativeBucketReceipts.json");
        var fixture = JObject.Parse(File.ReadAllText(path));
        var values = fixture["values"].ToObject<string[]>();
        var expected = fixture["sha256"].ToObject<string[]>();
        Assert.Equal(values.Length, expected.Length);
        for (int n = 1; n <= values.Length; n++)
        {
            var pairs = values.Take(n).Reverse().OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => new[] { x, new string('a', 64) }).ToArray();
            var json = JsonConvert.SerializeObject(pairs, Formatting.None);
            var digest = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(json)));
            Assert.Equal(expected[n - 1], digest);
        }
    }
}
