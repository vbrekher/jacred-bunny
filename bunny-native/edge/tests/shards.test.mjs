import test,{before,after} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {SHARD_KIND,parseShardConfig,makeShardProvider,allShardsReady,
 mergeShardRows,searchAllShards,ShardError} from "../src/shards.mjs";
import {isReady,searchLegacy} from "../src/sql.mjs";
import {verifyRemote} from "../live-acceptance.mjs";
import {verifyShardFleet,AcceptanceRefused} from "../src/acceptance.mjs";
import {createHandler} from "../src/app.mjs";

const BRIDGE=fileURLToPath(new URL("./fixtures/sqlite_bridge.py",import.meta.url));
const SPLIT=fileURLToPath(new URL("../../../tools/bunny_native_split.py",import.meta.url));
const KEY="jacred-sql-fanout-long-secret-key-0001";
function config(count=2){
  const env={JACRED_SQL_SHARD_IDS:Array.from({length:count},(_,i)=>i).join(",")};
  for(let i=0;i<count;i++){
    env["JACRED_SQL_SHARD_"+i+"_URL"]="libsql://db"+i+".lite.bunnydb.net";
    env["JACRED_SQL_SHARD_"+i+"_TOKEN"]="private-shard-"+i+"-long-secret-00000000";
  }
  return env;
}
const SHA="a".repeat(64);
function fakeDb(index,count=2,master=SHA,available=true){
  return {prepare(sql){
    if(!available)throw Error("Private URL libsql://SECRET");
    return {
      async first(){
        if(sql.includes("native_shard_manifest"))
          return {shard_index:index,shard_count:count,master_sha256:master};
        return {expected_buckets:1,actual_buckets:1,expected_torrents:1,
                actual_torrents:1,expected_tracks:0,actual_tracks:0};
      }
    };
  }};
}
function provider(list){
  return {kind:SHARD_KIND,shards:list.map((db,id)=>({id,db}))};
}
test("missing sharding config leaves existing single DB behavior",()=>{
  assert.equal(parseShardConfig({}),null);
  assert.equal(makeShardProvider({},()=>{throw Error("should not connect");}),null);
});
test("two, three, four contiguous shard IDs are accepted",()=>{
  for(const n of [2,3,4])assert.equal(parseShardConfig(config(n)).length,n);
});
test("partial, reordered, >4 or unknown shard IDs are refused",()=>{
  for(const ids of ["0","1,0","0,2","0,1,3","0,1,2,3,4","0,0"])
    assert.throws(()=>parseShardConfig({...config(),JACRED_SQL_SHARD_IDS:ids}),ShardError);
});
test("empty or malformed URL/token refuses before connecting",()=>{
  const bad=config();bad.JACRED_SQL_SHARD_0_URL="http://unsafe.example";
  assert.throws(()=>makeShardProvider(bad,()=>{throw Error("CONNECTED");}),/INVALID_SHARD_SECRET/);
  bad.JACRED_SQL_SHARD_0_URL="libsql://db0.lite.bunnydb.net";
  bad.JACRED_SQL_SHARD_1_TOKEN="too-short";
  assert.throws(()=>parseShardConfig(bad),ShardError);
});
test("duplicate database URLs are forbidden even under different shard indices",()=>{
  const env=config();
  env.JACRED_SQL_SHARD_1_URL=env.JACRED_SQL_SHARD_0_URL;
  assert.throws(()=>parseShardConfig(env),/DUPLICATE_SHARD_DATABASE/);
});
test("Bunny SDK receives separate explicit database credentials per shard",()=>{
  const captures=[];
  const p=makeShardProvider(config(3),input=>{
    captures.push(input);return fakeDb(captures.length-1,3);
  });
  assert.equal(p.kind,SHARD_KIND);
  assert.equal(p.shards.length,3);
  assert.equal(captures[1].url,"libsql://db1.lite.bunnydb.net");
  assert.ok(captures[2].authToken.startsWith("private-shard-2"));
  assert.equal(captures[0].timeout,2000);
  assert.equal(JSON.stringify(p).includes("private-shard-"),false);
});
test("all shards ready only when every manifest belongs to the same source",async()=>{
  assert.equal(await allShardsReady(provider([fakeDb(0),fakeDb(1)]),isReady),true);
  assert.equal(await isReady(provider([fakeDb(0),fakeDb(1)])),true);
  assert.equal(await isReady(provider([fakeDb(0,2),fakeDb(1,2,"b".repeat(64))])),false);
});
test("wrong shard index/count refuses readiness",async()=>{
  assert.equal(await isReady(provider([fakeDb(1),fakeDb(1)])),false);
  assert.equal(await isReady(provider([fakeDb(0,3),fakeDb(1,3)])),false);
});
test("outage of one shard is never interpreted as healthy partial catalog",async()=>{
  await assert.rejects(()=>isReady(provider([fakeDb(0),fakeDb(1,2,SHA,false)])),
                       /SHARD_UNAVAILABLE/);
});
test("duplicate URL chooses newer record and deterministic global order",()=>{
  const x=mergeShardRows([
    [{url:"https://test.invalid/z",updateTime:"2025-01-01",sid:1},
     {url:"https://test.invalid/a",sid:5}],
    [{url:"https://test.invalid/z",updateTime:"2026-01-01",sid:100}]
  ]);
  assert.deepEqual(x.map(t=>t.url),["https://test.invalid/a","https://test.invalid/z"]);
  assert.equal(x[1].sid,100);
});
test("global candidate limit is enforced including duplicate URLs",()=>{
  assert.throws(()=>mergeShardRows([[{url:"https://test.invalid/a"}],[{url:"https://test.invalid/a"}]],1),
                /GLOBAL_CANDIDATE_LIMIT_EXCEEDED/);
});
test("fanout splits candidate budget and never silently drops failed shard",async()=>{
  const budgets=[];
  const dbs=[{},{},{}];
  const p=provider(dbs);
  const out=await searchAllShards(p,{maxCandidates:10},async(db,args)=>{
    budgets.push(args.maxCandidates);
    return [{url:"https://test.invalid/"+budgets.length}];
  });
  assert.deepEqual(budgets,[4,3,3]);
  assert.equal(out.length,3);
  await assert.rejects(()=>searchAllShards(p,{maxCandidates:10},
      async(db,args)=>{if(db===dbs[1])throw Error("secret");return []}),
      /SHARD_UNAVAILABLE/);
});
test("shard provider has no direct prepare() so accidental unsharded reads fail",()=>{
  const p=provider([fakeDb(0),fakeDb(1)]);
  assert.equal(typeof p.prepare,"undefined");
});

let temp,db0,db1,handle;
function python(mode,where,payload=null){
  const r=spawnSync("python3",[BRIDGE,mode,where],{
    encoding:"utf8",input:payload===null?"":JSON.stringify(payload)+"\n",timeout:20000
  });
  if(r.status!==0)throw Error("SQLite offline test bridge failed");
  return JSON.parse(r.stdout.trim());
}
function sqlite(path){
  return {prepare(sql){
    return {
      async first(){return python("query",path,{sql,args:[],mode:"first"});},
      bind(...args){return {async all(){return python("query",path,{sql,args,mode:"all"});}};}
    };
  }};
}
before(()=>{
  temp=mkdtempSync(join(tmpdir(),"jacred-shards-sql-"));
  const full=python("create",temp).database;
  const split=spawnSync("python3",[SPLIT,"--database",full,
     "--output-dir",join(temp,"shards"),"--shards","2"],{encoding:"utf8",timeout:20000});
  if(split.status!==0)throw Error("SQLite shard split fixture failed: "+split.stderr);
  db0=sqlite(join(temp,"shards","shard-0.sqlite"));
  db1=sqlite(join(temp,"shards","shard-1.sqlite"));
  handle=createHandler({getDb:()=>provider([db0,db1]),env:{
    JACRED_EDGE_PREVIEW_ENABLED:"true",JACRED_EDGE_API_KEY:KEY
  }});
});
after(()=>{if(temp)rmSync(temp,{recursive:true,force:true});});
async function api(path){
  const r=await handle(new Request("https://edge.example"+path,{
    headers:{"X-Api-Key":KEY}
  }));
  const content=await r.text();
  return {status:r.status,body:content};
}
test("real GZip -> real split SQLite -> all-shards READY",async()=>{
  assert.equal(await isReady(provider([db0,db1])),true);
});
test("real SQL fanout recovers original Cyrillic title and its URL",async()=>{
  const r=await api("/api/v2.0/indexers/all/results?query="+encodeURIComponent("Щёлкунчик"));
  assert.equal(r.status,200);
  const body=JSON.parse(r.body);
  assert.equal(body.Results.length,1);
  assert.equal(body.Results[0].Details,"https://tracker.invalid/t/1");
});
test("real sharded Torznab season filtering keeps only S02E03",async()=>{
  const r=await api("/torznab/api?t=tvsearch&q="+encodeURIComponent("Сериал")+"&season=2&ep=3");
  assert.equal(r.status,200);
  assert.match(r.body,/Сериал S02E03/);
  assert.doesNotMatch(r.body,/Сериал S02E04/);
});
test("sharded health is 503 on one missing manifest, not partial success",async()=>{
  const faulty={prepare(sql){
    if(sql.includes("native_shard_manifest"))return {async first(){return null;}};
    return db1.prepare(sql);
  }};
  const h=createHandler({getDb:()=>provider([db0,faulty]),env:{
    JACRED_EDGE_PREVIEW_ENABLED:"true",JACRED_EDGE_API_KEY:KEY
  }});
  const response=await h(new Request("https://edge.example/health"));
  assert.equal(response.status,503);
});
test("sharded search rejects one shard outage, never delivers partial hits",async()=>{
  const broken={prepare(){throw Error("PRIVATE_DATABASE_ERROR_SECRET");}};
  const h=createHandler({getDb:()=>provider([db0,broken]),env:{
    JACRED_EDGE_PREVIEW_ENABLED:"true",JACRED_EDGE_API_KEY:KEY
  }});
  const response=await h(new Request("https://edge.example/api/v2.0/indexers/all/results?q=Series",
     {headers:{"X-Api-Key":KEY}}));
  assert.equal(response.status,503);
  assert.doesNotMatch(await response.text(),/PRIVATE_DATABASE_ERROR_SECRET/);
});


test("read-only fleet acceptance checks real sharded SQLite and original source identity",async()=>{
  const report=await verifyShardFleet(provider([db0,db1]),{
    verifySingle:verifyRemote,hashAll:true,auditRoute:true,maxRowsPerShard:50
  });
  assert.equal(report.status,"MULTI_DB_READ_ONLY_STAGING_PASS_NOT_CUTOVER");
  assert.equal(report.shards,2);
  assert.equal(report.master_sha256_consistent,true);
  assert.equal(report.totals.torrents,5);
  assert.equal(report.totals.buckets,4);
  assert.ok(report.totals.sql_reads>10);
  assert.equal(report.per_shard.length,2);
  assert.ok(report.per_shard.every(x=>x.payload_hash==="PASS"));
  assert.ok(report.per_shard.every(x=>x.routed_buckets_audited===x.buckets));
  assert.equal(report.production_cutover_authorized,false);
  assert.equal(JSON.stringify(report).includes("magnet:?"),false);
});
test("read-only fleet refuses insufficient full routing audit row budget",async()=>{
  await assert.rejects(
    verifyShardFleet(provider([db0,db1]),{
      verifySingle:verifyRemote,auditRoute:true,maxRowsPerShard:1
    }),
    /MULTI_SHARD_ACCEPTANCE_FAILED/
  );
});
test("read-only fleet detects altered bucket MD5 shard routing",async()=>{
  const swapped={prepare(sql){
    if(sql.includes("SELECT key_md5 FROM buckets")){
      return {bind(){return {async all(){return [{key_md5:"f".repeat(32)}];}}}};
    }
    return db1.prepare(sql);
  }};
  await assert.rejects(
    verifyShardFleet(provider([db0,swapped]),{
      verifySingle:verifyRemote,auditRoute:true,maxRowsPerShard:50
    }),
    /MULTI_SHARD_ACCEPTANCE_FAILED/
  );
});
test("read-only fleet fails closed when one Bunny database is offline",async()=>{
  const offline={prepare(){throw Error("PRIVATE_LIBSQL_URL_DO_NOT_PRINT");}};
  await assert.rejects(
    verifyShardFleet(provider([db0,offline]),{verifySingle:verifyRemote}),
    /SHARD_UNAVAILABLE/
  );
});
test("read-only fleet rejects any accidental SQL mutation in a verifier",async()=>{
  await assert.rejects(
    verifyShardFleet(provider([db0,db1]),{
      verifySingle:async db=>db.prepare("DELETE FROM torrents").first()
    }),
    /MULTI_SHARD_ACCEPTANCE_FAILED/
  );
});
test("operator CLI with routing scan flag without acknowledgement refuses offline",()=>{
  const cli=fileURLToPath(new URL("../live-acceptance.mjs",import.meta.url));
  const r=spawnSync(process.execPath,[cli,"--run","--audit-routing"],{
    encoding:"utf8",timeout:10000,env:{PATH:process.env.PATH}
  });
  assert.equal(r.status,2);
  assert.match(r.stderr,/JACRED_BUNNY_REMOTE_ACCEPTANCE_REFUSED/);
  assert.doesNotMatch(r.stderr,/PRIVATE_LIBSQL_URL/);
});
