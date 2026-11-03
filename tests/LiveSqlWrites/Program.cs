using System.Text.Json;
using System.Text.RegularExpressions;
using JacRed.Infrastructure.Persistence.Native;

string table=Environment.GetEnvironmentVariable("JACRED_TEST_TABLE")??"";
if(!Regex.IsMatch(table,"^jacred_acceptance_[a-f0-9]{32}$"))throw new Exception("Invalid private probe identity");
string url=Environment.GetEnvironmentVariable("JACRED_DATABASE_URL")??throw new Exception("Missing SQL URL");
string token=Environment.GetEnvironmentVariable("JACRED_DATABASE_WRITE_TOKEN")??throw new Exception("Missing scoped SQL writer");
string schema="CREATE TABLE "+table+"(id INTEGER PRIMARY KEY,value TEXT)";
using var client=new BunnySqlClient(url,token);
var existing=await client.Query(new SqlStatement("SELECT sql FROM sqlite_master WHERE name=?",table));
if(existing.Rows.Count!=0)throw new Exception("Probe name already exists; no adoption or overwrite");
var started=DateTime.UtcNow;bool created=false;
var results=new Dictionary<string,object>();
try
{
    try {await client.Query(new SqlStatement(schema));created=true;}
    catch
    {
        var found=await client.Query(new SqlStatement("SELECT sql FROM sqlite_master WHERE name=?",table));
        created=found.Rows.Count==1&&(string)found.Rows[0]["sql"]==schema;
        throw;
    }
    var statements=Enumerable.Range(0,1200).Select(i=>new SqlStatement("INSERT INTO "+table+" VALUES(?,?)",i,new string('x',4096))).ToArray();
    await client.PagedWrite(statements);
    var committed=(await client.Query(new SqlStatement("SELECT count(*) n,sum(length(value)) bytes FROM "+table))).Rows[0];
    if((long)committed["n"]!=1200||(long)committed["bytes"]!=1200L*4096)throw new Exception("Real paged write data mismatch");
    results["committed_rows"]=committed["n"];results["committed_payload_bytes"]=committed["bytes"];
    await client.Query(new SqlStatement("DELETE FROM "+table));
    bool rejected=false;
    try {await client.PagedWrite(Enumerable.Range(0,1200).Select(i=>new SqlStatement("INSERT INTO "+table+" VALUES(?,?)",i==1000?0:i,"rollback-test")).ToArray());}
    catch(NativeSqlException){rejected=true;}
    long after=(long)(await client.Query(new SqlStatement("SELECT count(*) n FROM "+table))).Rows[0]["n"];
    if(!rejected||after!=0)throw new Exception("Real late-page failure was not fully rolled back");
    results["later_page_constraint_failure_rejected"]=true;results["rows_after_failed_transaction"]=after;
}
finally
{
    if(created)
    {
        var found=await client.Query(new SqlStatement("SELECT sql FROM sqlite_master WHERE name=?",table));
        if(found.Rows.Count!=1||(string)found.Rows[0]["sql"]!=schema)throw new Exception("Probe schema changed; cleanup refused");
        await client.Query(new SqlStatement("DROP TABLE "+table));
        if((await client.Query(new SqlStatement("SELECT name FROM sqlite_master WHERE name=?",table))).Rows.Count!=0)throw new Exception("Probe cleanup not confirmed");
        results["owned_probe_cleanup_confirmed"]=true;
    }
}
results["source_sha"]=JacRed.VersionInfo.GitFullSha;results["elapsed_seconds"]=(DateTime.UtcNow-started).TotalSeconds;
results["scope"]="Real Bunny SQL paged writes using isolated owned probe table; no canonical catalog writes or deployed worker claim";
results["production_acceptance"]=false;
Console.WriteLine(JsonSerializer.Serialize(results));
