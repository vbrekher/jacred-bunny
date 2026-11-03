#!/usr/bin/env node
/**
 * JacRed v0.5.1: OPT-IN, READ-ONLY Bunny Database acceptance, run by operator.
 * NO external requests without --run; NEVER prints URL, token, raw JSON or magnets.
 * Requires dedicated staging database with read-only Bunny token.
 */
import {createHash} from "node:crypto";
import {isReady} from "./src/sql.mjs";

function safeNumber(v){
  const n=Number(v);
  if(!Number.isSafeInteger(n)||n<0)throw Error("Invalid remote count");
  return n;
}
export async function verifyRemote(db,{hashAll=false,maxRows=1000000}={}){
  const started=Date.now();
  if(!await isReady(db))throw Error("Missing or incomplete import manifest");
  const meta=await db.prepare(
    "SELECT bucket_count,torrent_count,track_file_count FROM native_imports WHERE id=1"
  ).first();
  if(!meta)throw Error("No remote import metadata");
  const expected=safeNumber(meta.torrent_count);
  const b=safeNumber(meta.bucket_count),t=safeNumber(meta.track_file_count);
  const aliasRow=await db.prepare("SELECT COUNT(*) AS n FROM bucket_aliases").first();
  const aliases=safeNumber(aliasRow?.n);
  if(aliases===0)throw Error("Missing original search aliases");
  const ftsRow=await db.prepare("SELECT COUNT(*) AS n FROM torrent_fts").first();
  if(safeNumber(ftsRow?.n)!==expected)throw Error("Search index count mismatch");
  // A real FTS MATCH query catches unsupported/missing FTS5 early. No content is
  // returned to CLI logs and zero matches for a random term are valid.
  await db.prepare("SELECT rowid FROM torrent_fts WHERE torrent_fts MATCH ? LIMIT 1")
    .bind('"jacredacceptance"').all();
  const summary={
    result:"READ_ONLY_REMOTE_STAGING_CHECK_PASS_NOT_PRODUCTION_CUTOVER",
    source:"REAL_BUNNY_DATABASE_ONLY_WHEN_RUN_WITH_OPERATOR_CREDENTIALS",
    rows:expected,buckets:b,track_inventory:t,aliases,
    sqlite_fts5_query:"PASS",full_payload_sha256_audit:hashAll?"PENDING":"NOT_RUN",
    remote_fleet_parity:"NOT_TESTED",http_edge_deployment:"NOT_TESTED",
    production_cutover_authorized:false
  };
  if(hashAll){
    if(!Number.isSafeInteger(maxRows)||maxRows<1||maxRows>10000000)
      throw Error("Invalid audit row safety limit");
    if(expected>maxRows)throw Error("Full hash audit exceeds maxRows cap");
    let last=0,count=0;
    for(;;){
      const batch=await db.prepare(
        "SELECT id,raw_json,raw_sha256 FROM torrents WHERE id>? ORDER BY id LIMIT ?"
      ).bind(last,100).all();
      if(!Array.isArray(batch))throw Error("Invalid result array");
      if(batch.length===0)break;
      for(const row of batch){
        if(typeof row.raw_json!=="string"||typeof row.raw_sha256!=="string")
          throw Error("Missing remote payload/hash");
        const actual=createHash("sha256").update(row.raw_json,"utf8").digest("hex");
        if(actual!==row.raw_sha256)throw Error("Remote payload checksum mismatch");
        const id=safeNumber(row.id);
        if(id<=last)throw Error("Remote scan order is unstable");
        last=id;count++;
      }
      if(count>maxRows)throw Error("Remote scan exceeded row cap");
    }
    if(count!==expected)throw Error("Full payload count mismatch");
    summary.full_payload_sha256_audit="PASS";
    summary.rows_hashed=count;
  }
  summary.elapsed_ms=Date.now()-started;
  return summary;
}
function parseArgs(argv){
  if(argv.includes("--help")||argv.length===0)return {help:true};
  if(!argv.includes("--run"))throw Error("Explicit --run required");
  for(const flag of argv)
    if(!["--run","--hash-all","--ack-full-scan"].includes(flag))
      throw Error("Unknown argument");
  const hashAll=argv.includes("--hash-all");
  if(hashAll&&!argv.includes("--ack-full-scan"))
    throw Error("--hash-all requires --ack-full-scan");
  return {help:false,hashAll};
}
async function main(){
  try{
    const args=parseArgs(process.argv.slice(2));
    if(args.help){
      console.log("Usage: node live-acceptance.mjs --run [--hash-all --ack-full-scan]");
      console.log("Read-only staging checks. Requires BUNNY_DATABASE_URL and BUNNY_DATABASE_AUTH_TOKEN in environment.");
      return;
    }
    if(!process.env.BUNNY_DATABASE_URL||!process.env.BUNNY_DATABASE_AUTH_TOKEN)
      throw Error("Missing Bunny Database connection environment");
    const {connect}=await import("@bunny.net/database-client");
    const db=connect({timeout:3000});
    const result=await verifyRemote(db,{hashAll:args.hashAll});
    console.log(JSON.stringify(result));
  }catch(error){
    // SDK exception messages can echo SQL, raw magnets or connection strings.
    // Intentionally only emit generic failure status and non-secret error type.
    console.error("JACRED_BUNNY_REMOTE_ACCEPTANCE_REFUSED",error?.name??"Error");
    process.exitCode=2;
  }
}
if(process.argv[1]&&import.meta.url===new URL("file://"+process.argv[1]).href)
  main();
