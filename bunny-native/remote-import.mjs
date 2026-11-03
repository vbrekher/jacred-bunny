#!/usr/bin/env node
/**
 * Optional isolated Bunny Database write adapter for JacRed v0.4.
 * NEVER touches a running .NET JacRed or an existing app database.
 * --apply requires a fresh empty Bunny Database, explicit --ack-new-database,
 * and the verified SHA256 of an export from bunny_native_export.py.
 */
import { createReadStream, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const here=dirname(fileURLToPath(import.meta.url));
const migration=resolve(here,"migrations");
const tables={bucket:0,alias:0,torrent:0,track:0};

function parseArgs(args) {
  let result={};
  for(let i=0;i<args.length;i++){
    if(args[i]==="--apply"||args[i]==="--ack-new-database") result[args[i]]=true;
    else if(["--file","--sha256"].includes(args[i])){
      if(!args[i+1])throw Error("Missing CLI value");
      result[args[i]]=args[++i];
    } else throw Error("Unknown CLI argument");
  }
  if(!result["--file"]||!/^[0-9a-f]{64}$/.test(result["--sha256"]??""))
    throw Error("Require --file and exact 64-character --sha256");
  if(result["--apply"]&&!result["--ack-new-database"])
    throw Error("--apply requires --ack-new-database");
  return result;
}
async function validateFile(file,expected) {
  const hash=createHash("sha256");
  for await(const chunk of createReadStream(file)) hash.update(chunk);
  if(hash.digest("hex")!==expected)throw Error("Export SHA256 mismatch");
  let first=true,ended=false,header=null;
  const totals={bucket:0,alias:0,torrent:0,track:0};
  for await (const line of createInterface({input:createReadStream(file),crlfDelay:Infinity})){
    if(!line)throw Error("Blank JSONL line");
    const record=JSON.parse(line);
    if(first){
      if(record.kind!=="header"||record.schema!=="jacred-bunny-v0.4")throw Error("Invalid export header");
      header=record;first=false;continue;
    }
    if(record.kind==="end"){
      if(ended||Object.keys(totals).some(k=>record.counts?.[k]!==totals[k]))
        throw Error("Export trailer mismatch");
      ended=true;continue;
    }
    if(ended||!(record.kind in totals))throw Error("Unexpected export row");
    totals[record.kind]++;
    if(record.kind==="torrent"){
      const sum=createHash("sha256").update(record.raw_json,"utf8").digest("hex");
      if(sum!==record.raw_sha256)throw Error("Payload hash mismatch");
    }
  }
  if(!/^[0-9a-f]{64}$/.test(header.master_sha256??""))
    throw Error("Missing stage master checksum");
  const sharded=header.shard_index!==undefined||header.shard_count!==undefined;
  if(sharded&&!(Number.isInteger(header.shard_index)&&
       Number.isInteger(header.shard_count)&&header.shard_count>=2&&
       header.shard_count<=4&&header.shard_index>=0&&
       header.shard_index<header.shard_count))
    throw Error("Invalid shard manifest in export");
  if(!ended||totals.bucket!==header.bucket_count||totals.torrent!==header.torrent_count
    ||totals.track!==header.track_file_count)throw Error("Export incomplete");
  return {totals,header};
}

const QUERIES={
  bucket:"INSERT INTO buckets(bucket_key,key_md5,update_time,file_time,shard_sha256,torrent_count) VALUES(?,?,?,?,?,?) ON CONFLICT(bucket_key) DO UPDATE SET key_md5=excluded.key_md5,update_time=excluded.update_time,file_time=excluded.file_time,shard_sha256=excluded.shard_sha256,torrent_count=excluded.torrent_count",
  alias:"INSERT INTO bucket_aliases(alias,bucket_key) VALUES(?,?) ON CONFLICT(alias,bucket_key) DO NOTHING",
  torrent:"INSERT INTO torrents(bucket_key,source_url,tracker_name,title,name,original_name,magnet,update_time,raw_json,raw_sha256) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bucket_key,source_url) DO UPDATE SET tracker_name=excluded.tracker_name,title=excluded.title,name=excluded.name,original_name=excluded.original_name,magnet=excluded.magnet,update_time=excluded.update_time,raw_json=excluded.raw_json,raw_sha256=excluded.raw_sha256",
  track:"INSERT INTO track_file_inventory(relative_path,bytes,sha256) VALUES(?,?,?) ON CONFLICT(relative_path) DO UPDATE SET bytes=excluded.bytes,sha256=excluded.sha256",
};
const FIELDS={
  bucket:["bucket_key","key_md5","update_time","file_time","shard_sha256","torrent_count"],
  alias:["alias","bucket_key"],
  torrent:["bucket_key","source_url","tracker_name","title","name","original_name","magnet","update_time","raw_json","raw_sha256"],
  track:["relative_path","bytes","sha256"],
};
async function apply(db,file,stats){
  const tablesAlready=await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all();
  if(tablesAlready.length)throw Error("Target database already has JacRed tables; refusing overwrite/resume");
  await db.exec(readFileSync(join(migration,"0001_catalog.sql"),"utf8"));
  await db.exec(readFileSync(join(migration,"0002_search_fts5.sql"),"utf8"));
  if(stats.header.shard_count!==undefined)
    await db.exec(readFileSync(join(migration,"0003_shard_identity.sql"),"utf8"));
  let pending=[],kind=null,total=0;
  const flush=async()=>{
    if(!pending.length)return;
    // Bunny client transactions are all-or-nothing for each bounded batch.
    await db.batch(pending);
    pending=[];
  };
  for await (const line of createInterface({input:createReadStream(file),crlfDelay:Infinity})){
    const row=JSON.parse(line);
    if(row.kind==="header"||row.kind==="end")continue;
    if(kind!==row.kind){await flush();kind=row.kind;}
    pending.push(db.prepare(QUERIES[row.kind]).bind(...FIELDS[row.kind].map(key=>row[key]??null)));
    total++;
    if(pending.length>=50)await flush();
  }
  await flush();
  const result=await db.prepare("SELECT (SELECT count(*) FROM buckets) AS buckets,(SELECT count(*) FROM torrents) AS torrents,(SELECT count(*) FROM track_file_inventory) AS tracks").all();
  const row=result[0];
  if(Number(row.buckets)!==stats.totals.bucket||Number(row.torrents)!==stats.totals.torrent||Number(row.tracks)!==stats.totals.track)
    throw Error("Bunny remote count mismatch; database is staging-only");
  // Shard identity is published before native_imports. A partial upload
  // still cannot satisfy the Edge readiness gate.
  if(stats.header.shard_count!==undefined){
    await db.prepare("INSERT INTO native_shard_manifest(id,shard_index,shard_count,master_sha256,created_at) VALUES(1,?,?,?,?)")
      .bind(stats.header.shard_index,stats.header.shard_count,
        stats.header.master_sha256,new Date().toISOString()).run();
  }
  // Publish the import manifest only after all batches and counts succeeded.
  // Edge preview refuses a partial upload with no import manifest.
  await db.prepare("INSERT INTO native_imports(id,master_sha256,bucket_count,torrent_count,track_file_count,imported_at) VALUES(1,?,?,?,?,?)")
    .bind(stats.header.master_sha256,stats.totals.bucket,stats.totals.torrent,
      stats.totals.track,new Date().toISOString()).run();
  // No complete remote record-by-record hash or cross-region parity certification.
  // Deliberately do not record any 'production ready' or cutover flag.
  return {status:"BUNNY_REMOTE_STAGING_ONLY_NOT_CUTOVER",statements:total,
    counts:stats.totals,full_remote_content_parity:"NOT_TESTED"};
}
async function main(){
  const args=parseArgs(process.argv.slice(2));
  const file=resolve(args["--file"]);
  if(statSync(file).size>900_000_000)throw Error("Export too large for single Bunny Database staging (900MB guard)");
  const stats=await validateFile(file,args["--sha256"]);
  if(!args["--apply"]){
    console.log(JSON.stringify({status:"BUNNY_REMOTE_DRY_RUN_NO_NETWORK",counts:stats.totals,
      checksum:"PASS",remote_backend:"NOT_TESTED"}));
    return;
  }
  if(!process.env.BUNNY_DATABASE_URL||!process.env.BUNNY_DATABASE_AUTH_TOKEN)
    throw Error("Missing BUNNY_DATABASE_URL/BUNNY_DATABASE_AUTH_TOKEN");
  // Only load official SDK after dry-run; no dependency needed for offline checks.
  const {connect}=await import("@bunny.net/database-client");
  const db=connect();
  console.log(JSON.stringify(await apply(db,file,stats)));
}
main().catch(error=>{
  // Never print SDK error text or raw URLs: can contain server-echoed SQL / secrets.
  console.error("BUNNY_REMOTE_IMPORT_REFUSED:",error.name||"Error");
  process.exitCode=2;
});
