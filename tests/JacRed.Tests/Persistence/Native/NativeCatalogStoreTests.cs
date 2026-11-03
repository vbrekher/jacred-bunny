using System.Diagnostics;
using System.Net;
using System.Text;
using JacRed.Infrastructure.Persistence.Native;
using JacRed.Infrastructure.Persistence;
using JacRed.Models.Details;
using Xunit;

namespace JacRed.Tests.Persistence.Native;

// SQLite validates SQL semantics; this bridge does not establish live Bunny acceptance.
public sealed class NativeCatalogStoreTests : IDisposable
{
    readonly string directory = Path.Combine(Path.GetTempPath(), "jacred-native-" + Guid.NewGuid().ToString("N"));
    readonly SqliteHandler handler;
    readonly BunnySqlClient client;
    readonly NativeCatalogStore store;

    public NativeCatalogStoreTests()
    {
        Directory.CreateDirectory(directory);
        var database = Path.Combine(directory, "fixture.sqlite");
        var root = FindRoot();
        var migrations = new[] { "0001_catalog.sql", "0002_search_fts5.sql", "0004_catalog_commits.sql" }
            .Select(name => File.ReadAllText(Path.Combine(root, "bunny-native/migrations", name)));
        using var setup = new Process { StartInfo = new ProcessStartInfo("python3")
        { RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false } };
        setup.StartInfo.ArgumentList.Add("-c");
        setup.StartInfo.ArgumentList.Add("import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);db.executescript(sys.stdin.read());db.close()");
        setup.StartInfo.ArgumentList.Add(database);
        setup.Start();setup.StandardInput.Write(string.Join("\n", migrations));setup.StandardInput.Close();setup.WaitForExit();
        Assert.Equal(0,setup.ExitCode);
        handler = new SqliteHandler(database, Path.Combine(AppContext.BaseDirectory,"Fixtures/NativeSql/hrana_sqlite.py"));
        client = new BunnySqlClient("libsql://fixture.lite.bunnydb.net", "test-token-only", handler);
        store = new NativeCatalogStore(client);
    }

    [Fact]
    public async Task Snapshot_is_stable_and_rejects_corrupt_rows_and_bucket_receipts()
    {
        var bucket=await store.ReadBucket("snow:snow");
        var torrent=Torrent();
        await store.WriteBucket(bucket,new Dictionary<string,TorrentDetails>{{torrent.url,torrent}},Guid.NewGuid().ToString("N"));
        var first=await store.SnapshotProof();
        Assert.Equal(1,first.Counts["torrents"]);
        Assert.Equal(first.Sha256,(await store.SnapshotProof()).Sha256);
        Assert.Matches("^[0-9a-f]{64}$",first.Sha256);
        var exported=await client.Transaction(NativeCatalogStore.SnapshotTables.Select(x=>new SqlStatement("SELECT * FROM "+x.Table+" ORDER BY "+x.Order)).ToArray(),write:false);
        var snapshot=Path.Combine(directory,"canonical.json");
        File.WriteAllText(snapshot,System.Text.Json.JsonSerializer.Serialize(new { schema="jacred-canonical-golden-snapshot/v1",tables=NativeCatalogStore.SnapshotTables.Select((x,i)=>(x.Table,exported[i].Rows)).ToDictionary(x=>x.Table,x=>x.Rows) }));
        using var verifier=new Process { StartInfo=new ProcessStartInfo("python3") { RedirectStandardOutput=true,RedirectStandardError=true,UseShellExecute=false } };
        verifier.StartInfo.ArgumentList.Add(Path.Combine(FindRoot(),"tools/bunny_snapshot_checksum.py"));
        verifier.StartInfo.ArgumentList.Add(snapshot);verifier.Start();
        var output=await verifier.StandardOutput.ReadToEndAsync();await verifier.WaitForExitAsync();
        Assert.Equal(0,verifier.ExitCode);
        Assert.Equal(first.Sha256,System.Text.Json.JsonDocument.Parse(output).RootElement.GetProperty("snapshotSha256").GetString());
        await client.Query(new("UPDATE buckets SET torrent_count=99"));
        await Assert.ThrowsAsync<NativeSqlException>(()=>store.SnapshotProof());
        await client.Query(new("UPDATE buckets SET torrent_count=1"));
        await client.Query(new("UPDATE torrents SET raw_json='corrupt-private-payload'"));
        var error=await Assert.ThrowsAsync<NativeSqlException>(()=>store.SnapshotProof());
        Assert.DoesNotContain("corrupt-private-payload",error.Message);
    }

    static string FindRoot()
    {
        for (var current = new DirectoryInfo(AppContext.BaseDirectory); current != null; current = current.Parent)
            if (File.Exists(Path.Combine(current.FullName, "JacRed.csproj"))) return current.FullName;
        throw new InvalidOperationException("Test repository not found");
    }

    static TorrentDetails Torrent(string url = "https://example.invalid/1") => new()
    {
        url = url, trackerName = "synthetic-fixture", title = "JacRed Ёж 雪", name = "Ёж", originalname = "Snow",
        updateTime = DateTime.UtcNow, magnet = "magnet:?xt=urn:btih:" + new string('a',40),
        languages = new HashSet<string> { "ru", "ja" }, voices = new HashSet<string> { "voice1" }, seasons = new HashSet<int> { 1,2 }, quality = 2160
    };

    [Fact]
    public async Task Runtime_schema_requires_exact_embedded_migration_receipts()
    {
        var names=new[]{"0001_catalog.sql","0002_search_fts5.sql","0004_catalog_commits.sql"};
        await client.Query(new("CREATE TABLE native_schema_migrations(name TEXT PRIMARY KEY,sha256 TEXT NOT NULL)"));
        foreach(var name in names)
        {
            using var stream=typeof(NativeCatalogStore).Assembly.GetManifestResourceStream("JacRed.Native.Migrations."+name);
            Assert.NotNull(stream);
            await client.Query(new("INSERT INTO native_schema_migrations VALUES(?,?)",name,Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(stream))));
        }
        await store.VerifySchema();
        await client.Query(new("UPDATE native_schema_migrations SET sha256=? WHERE name=?",new string('0',64),names[2]));
        await Assert.ThrowsAsync<NativeSqlException>(()=>store.VerifySchema());
    }

    [Fact]
    public async Task Full_torrent_fields_survive_atomic_insert_update_delete_and_fts()
    {
        var empty = await store.ReadBucket("ёж:snow");
        var torrent = Torrent();var records = new Dictionary<string,TorrentDetails> { [torrent.url]=torrent };
        var id = Guid.NewGuid().ToString("N");
        Assert.Equal(1,await store.WriteBucket(empty,records,id));
        Assert.Equal(1,await store.WriteBucket(empty,records,id)); // Same immutable receipt, no duplicate delivery.
        var loaded = await store.ReadBucket(empty.Key);
        Assert.Equal(2160,loaded.Torrents[torrent.url].quality);
        Assert.Equal(torrent.languages,loaded.Torrents[torrent.url].languages);
        Assert.Equal(torrent.seasons,loaded.Torrents[torrent.url].seasons);
        Assert.Equal(1L,(await client.Query(new("SELECT count(*) AS n FROM torrent_fts WHERE torrent_fts MATCH ?","JacRed"))).Rows[0]["n"]);
        loaded.Torrents[torrent.url].title="Changed Ёж";
        Assert.Equal(2,await store.WriteBucket(loaded,loaded.Torrents,Guid.NewGuid().ToString("N")));
        var changed = await store.ReadBucket(empty.Key);
        Assert.Equal("Changed Ёж",changed.Torrents[torrent.url].title);
        Assert.Equal(3,await store.WriteBucket(changed,new Dictionary<string,TorrentDetails>(),Guid.NewGuid().ToString("N")));
        var deleted=await store.ReadBucket(empty.Key);
        Assert.Empty(deleted.Torrents);Assert.Equal(3,deleted.Revision);
        Assert.Equal(0L,(await client.Query(new("SELECT count(*) AS n FROM torrents"))).Rows[0]["n"]);
        Assert.Equal(0L,(await client.Query(new("SELECT count(*) AS n FROM catalog_write_guards"))).Rows[0]["n"]);
    }

    [Fact]
    public async Task Original_filedb_merge_path_commits_to_SQL_and_preserves_delete()
    {
        var key="native-test-"+Guid.NewGuid().ToString("N")+":snow";
        var incoming=Torrent();incoming.title="JacRed Ёж / Snow (2024) WEB-DL 2160p";incoming.types=new[]{"movie"};
        using(var scope=FileDB.OpenNativeWrite(store,key)) scope.AddOrUpdate(incoming);
        var saved=await store.ReadBucket(key);
        Assert.Single(saved.Torrents);Assert.Equal(2160,saved.Torrents[incoming.url].quality);
        using(var scope=FileDB.OpenNativeWrite(store,key))
        {
            scope.Database.Clear();scope.savechanges=true;
        }
        Assert.Empty((await store.ReadBucket(key)).Torrents);
        Assert.False(File.Exists(FileDB.PathForKey(key)));
    }

    [Fact]
    public async Task Concurrent_writers_cannot_replace_newer_bucket_from_stale_snapshot()
    {
        var first=await store.ReadBucket("race:race");var second=await store.ReadBucket(first.Key);
        var a=Torrent("https://example.invalid/a");var b=Torrent("https://example.invalid/b");
        async Task<bool> Attempt(CatalogBucket bucket,TorrentDetails row)
        {
            try { await store.WriteBucket(bucket,new Dictionary<string,TorrentDetails>{[row.url]=row},Guid.NewGuid().ToString("N"));return true; }
            catch(NativeSqlException) { return false; }
        }
        var success=await Task.WhenAll(Attempt(first,a),Attempt(second,b));
        Assert.Single(success,x=>x);
        var actual=await store.ReadBucket(first.Key);Assert.Single(actual.Torrents);Assert.Equal(1,actual.Revision);
        Assert.Equal(1L,(await client.Query(new("SELECT count(*) AS n FROM catalog_commits"))).Rows[0]["n"]);
    }

    [Fact]
    public async Task Original_lostfilm_rename_moves_source_and_target_atomically()
    {
        var torrent=Torrent();torrent.trackerName="lostfilm";torrent.name="Old Name";torrent.originalname="Old Name";torrent.types=new[]{"tvseries"};
        var oldKey=FileDB.KeyForTorrent(torrent.name,torrent.originalname);
        using(var scope=FileDB.OpenNativeWrite(store,oldKey))scope.AddOrUpdate(torrent);
        torrent.name="New Name";torrent.originalname="New Name";
        using(var scope=FileDB.OpenNativeWrite(store,oldKey))scope.AddOrUpdate(torrent);
        Assert.Empty((await store.ReadBucket(oldKey)).Torrents);
        Assert.Single((await store.ReadBucket(FileDB.KeyForTorrent(torrent.name,torrent.originalname))).Torrents);
        Assert.Equal(1L,(await client.Query(new("SELECT count(*) AS n FROM torrents"))).Rows[0]["n"]);
    }

    [Fact]
    public async Task Merge_exception_aborts_scope_instead_of_committing_earlier_partial_changes()
    {
        var row=Torrent();row.types=new[]{"movie"};var key="abort:abort";
        using(var scope=FileDB.OpenNativeWrite(store,key))scope.AddOrUpdate(row);
        var before=await store.ReadBucket(key);
        using(var scope=FileDB.OpenNativeWrite(store,key))
        {
            row.title="Must not commit";scope.AddOrUpdate(row);
            Assert.Throws<ArgumentNullException>(()=>scope.AddOrUpdate(new TorrentDetails{url=null}));
        }
        var after=await store.ReadBucket(key);
        Assert.Equal(before.Revision,after.Revision);
        Assert.Equal(before.Torrents[row.url].title,after.Torrents[row.url].title);
    }

    [Fact]
    public async Task Atomic_move_rejects_stale_destination_without_deleting_source()
    {
        var source=await store.ReadBucket("source:source");var target=await store.ReadBucket("target:target");
        var torrent=Torrent();var rows=new Dictionary<string,TorrentDetails>{{torrent.url,torrent}};
        await store.WriteBucket(source,rows,Guid.NewGuid().ToString("N"));source=await store.ReadBucket(source.Key);
        var competitor=Torrent("https://example.invalid/competitor");
        await store.WriteBucket(target,new Dictionary<string,TorrentDetails>{{competitor.url,competitor}},Guid.NewGuid().ToString("N"));
        var id=Guid.NewGuid().ToString("N");
        await Assert.ThrowsAsync<NativeSqlException>(()=>store.WriteBuckets(new[]{new CatalogWrite(source,new Dictionary<string,TorrentDetails>()),new CatalogWrite(target,rows)},id));
        Assert.Single((await store.ReadBucket(source.Key)).Torrents);
        var currentTarget=await store.ReadBucket(target.Key);
        var combined=new Dictionary<string,TorrentDetails>(currentTarget.Torrents){{torrent.url,torrent}};
        var writes=new[]{new CatalogWrite(source,new Dictionary<string,TorrentDetails>()),new CatalogWrite(currentTarget,combined)};
        var committed=await store.WriteBuckets(writes,id);
        Assert.Equal(committed,await store.WriteBuckets(writes,id));
        Assert.Empty((await store.ReadBucket(source.Key)).Torrents);
        Assert.Equal(2,(await store.ReadBucket(target.Key)).Torrents.Count);
    }

    [Fact]
    public async Task Lost_commit_response_is_reconciled_without_replacing_current_data()
    {
        var snapshot=await store.ReadBucket("lost:lost");var torrent=Torrent();var id=Guid.NewGuid().ToString("N");
        var rows=new Dictionary<string,TorrentDetails>{[torrent.url]=torrent};
        handler.DropNextBatchResponse=true;
        var error=await Assert.ThrowsAsync<NativeSqlException>(()=>store.WriteBucket(snapshot,rows,id));
        Assert.DoesNotContain("test-token-only",error.Message);
        Assert.Equal(1,await store.WriteBucket(snapshot,rows,id));
        Assert.Single((await store.ReadBucket(snapshot.Key)).Torrents);
        rows[torrent.url].title="Different payload";
        await Assert.ThrowsAsync<NativeSqlException>(()=>store.WriteBucket(snapshot,rows,id));
    }

    [Fact]
    public async Task Failed_begin_cannot_fall_through_to_autocommit_writes()
    {
        handler.FailBegin=true;
        var error=await Assert.ThrowsAsync<NativeSqlException>(()=>client.Transaction(new[] {new SqlStatement("INSERT INTO buckets VALUES('bad','bad',NULL,0,'bad',0)")}));
        Assert.DoesNotContain("private-fixture-value",error.Message);
        handler.FailBegin=false;
        Assert.Equal(0L,(await client.Query(new("SELECT count(*) AS n FROM buckets"))).Rows[0]["n"]);
    }

    [Fact]
    public async Task Statement_failure_rolls_back_earlier_effects_and_receipts()
    {
        await Assert.ThrowsAsync<NativeSqlException>(()=>client.Transaction(new[] {
            new SqlStatement("INSERT INTO buckets VALUES('bad','bad',NULL,0,'bad',0)"),new SqlStatement("INSERT INTO missing VALUES(1)") }));
        Assert.Equal(0L,(await client.Query(new("SELECT count(*) AS n FROM buckets"))).Rows[0]["n"]);
    }

    [Theory]
    [InlineData("http://fixture.bunnydb.net")]
    [InlineData("https://fixture.bunnydb.net.attacker.invalid")]
    [InlineData("https://user:secret@fixture.bunnydb.net")]
    [InlineData("https://fixture.bunnydb.net?token=secret")]
    [InlineData("https://fixture.bunnydb.net:444")]
    public void Arbitrary_hosts_or_credential_urls_are_refused(string url)
        =>Assert.Throws<NativeSqlException>(()=>new BunnySqlClient(url,"test"));

    public void Dispose() { client.Dispose();Directory.Delete(directory,true); }

    sealed class SqliteHandler(string database,string script) : HttpMessageHandler
    {
        public bool FailBegin;
        public bool DropNextBatchResponse;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken cancellationToken)
        {
            Assert.Equal("fixture.lite.bunnydb.net",request.RequestUri.Host);
            Assert.Equal("test-token-only",request.Headers.Authorization.Parameter);
            var input=await request.Content.ReadAsStringAsync(cancellationToken);
            using var process=new Process {StartInfo=new ProcessStartInfo("python3")
                {RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,UseShellExecute=false}};
            process.StartInfo.ArgumentList.Add(script);process.StartInfo.ArgumentList.Add(database);
            process.StartInfo.ArgumentList.Add(FailBegin?"fail_begin":"normal");
            process.Start();await process.StandardInput.WriteAsync(input);process.StandardInput.Close();
            var output=await process.StandardOutput.ReadToEndAsync(cancellationToken);
            await process.WaitForExitAsync(cancellationToken);Assert.Equal(0,process.ExitCode);
            if(DropNextBatchResponse&&input.Contains("\"type\":\"batch\"",StringComparison.Ordinal))
            {DropNextBatchResponse=false;throw new HttpRequestException("Simulated response loss after database commit");}
            return new HttpResponseMessage(HttpStatusCode.OK){Content=new StringContent(output,Encoding.UTF8,"application/json")};
        }
    }
}
