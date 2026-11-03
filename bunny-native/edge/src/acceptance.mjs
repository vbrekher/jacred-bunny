/**
 * JacRed Bunny Native v0.5.3: operator-initiated read-only multi-DB acceptance.
 * This is staging evidence, never automatic production authorization.
 */
import {isReady} from "./sql.mjs";
import {isShardProvider,ShardError} from "./shards.mjs";

const HASH=/^[0-9a-f]{32}$/;
const SOURCE=/^[0-9a-f]{64}$/;
const SCAN_BATCH=100;
export class AcceptanceRefused extends Error {
  constructor(code){super(code);this.name="AcceptanceRefused";this.code=code;}
}
function safeInt(value){
  const n=Number(value);
  if(!Number.isSafeInteger(n)||n<0)throw new AcceptanceRefused("INVALID_REMOTE_COUNT");
  return n;
}
function instrument(db){
  const stats={sql_reads:0,sql_time_ms:0};
  const measured=async operation=>{
    const begin=Date.now();
    try{return await operation();}
    finally{stats.sql_time_ms+=Date.now()-begin;}
  };
  function statement(query){
    if(typeof query!=="string"||!/^\s*SELECT\b/i.test(query))
      throw new AcceptanceRefused("NON_READ_ONLY_SQL_ATTEMPT");
    stats.sql_reads++;
    const stmt=db.prepare(query);
    const methods={
      async first(){return measured(()=>stmt.first());},
      async all(){return measured(()=>stmt.all());},
      bind(...args){
        const bound=stmt.bind(...args);
        return {
          async first(){return measured(()=>bound.first());},
          async all(){return measured(()=>bound.all());}
        };
      }
    };
    return methods;
  }
  return {db:{prepare:statement},stats};
}
async function auditRouting(db,index,count,limit){
  let scanned=0,last="",seen=new Set();
  for(;;){
    const rows=await db.prepare(
      "SELECT key_md5 FROM buckets WHERE key_md5>? ORDER BY key_md5 LIMIT ?"
    ).bind(last,SCAN_BATCH).all();
    if(!Array.isArray(rows)||rows.length>SCAN_BATCH)
      throw new AcceptanceRefused("INVALID_ROUTE_SCAN");
    if(rows.length===0)break;
    for(const item of rows){
      const hash=item?.key_md5;
      if(typeof hash!=="string"||!HASH.test(hash)||hash<=last||seen.has(hash))
        throw new AcceptanceRefused("INVALID_BUCKET_HASH_ORDER");
      if(Number(BigInt("0x"+hash)%BigInt(count))!==index)
        throw new AcceptanceRefused("BUCKET_ROUTING_MISMATCH");
      seen.add(hash);last=hash;scanned++;
      if(scanned>limit)throw new AcceptanceRefused("ROUTE_AUDIT_LIMIT_EXCEEDED");
    }
  }
  return scanned;
}
/**
 * @param {object} provider - strict 2..4 shard provider
 * @param {object} options - verifySingle is the existing v0.5.1 read-only verifier
 */
export async function verifyShardFleet(provider,{
  verifySingle,hashAll=false,auditRoute=false,maxRowsPerShard=1000000
}={}){
  if(!isShardProvider(provider)||provider.shards.length<2||provider.shards.length>4)
    throw new AcceptanceRefused("MULTI_DB_TOPOLOGY_REQUIRED");
  if(typeof verifySingle!=="function")throw new AcceptanceRefused("VERIFIER_REQUIRED");
  if(!Number.isInteger(maxRowsPerShard)||maxRowsPerShard<1||maxRowsPerShard>10000000)
    throw new AcceptanceRefused("UNSAFE_ROW_LIMIT");
  const start=Date.now();
  const wrapped=provider.shards.map(({id,db})=>({id,...instrument(db)}));
  const wrappedProvider={kind:provider.kind,shards:wrapped.map(({id,db})=>({id,db}))};
  // Reject absent, mismatched, incomplete or unavailable shards BEFORE scanning.
  if(!await isReady(wrappedProvider))throw new AcceptanceRefused("SHARD_FLEET_NOT_READY");
  const sample=await Promise.allSettled(wrapped.map(async ({id,db})=>{
    const manifest=await db.prepare(
      "SELECT shard_index,shard_count,master_sha256 FROM native_shard_manifest WHERE id=1"
    ).first();
    if(!manifest||safeInt(manifest.shard_index)!==id||
       safeInt(manifest.shard_count)!==wrapped.length||
       !SOURCE.test(manifest.master_sha256??""))
      throw new AcceptanceRefused("INVALID_SHARD_MANIFEST");
    const summary=await verifySingle(db,{hashAll,maxRows:maxRowsPerShard});
    let bucketsAudited=null;
    if(auditRoute){
      if(safeInt(summary.buckets)>maxRowsPerShard)
        throw new AcceptanceRefused("ROUTE_AUDIT_LIMIT_EXCEEDED");
      bucketsAudited=await auditRouting(db,id,wrapped.length,maxRowsPerShard);
      if(bucketsAudited!==safeInt(summary.buckets))
        throw new AcceptanceRefused("BUCKET_COUNT_DIVERGENCE");
    }
    return {shard_index:id,master_sha256:manifest.master_sha256,
      ...summary,routed_buckets_audited:bucketsAudited};
  }));
  if(sample.some(s=>s.status!=="fulfilled"))
    throw new AcceptanceRefused("MULTI_SHARD_ACCEPTANCE_FAILED");
  const results=sample.map(s=>s.value);
  if(new Set(results.map(r=>r.master_sha256)).size!==1)
    throw new AcceptanceRefused("MIXED_SOURCE_SNAPSHOT");
  const totals={buckets:0,torrents:0,track_inventory:0,aliases:0,sql_reads:0,sql_time_ms:0};
  results.forEach((r,i)=>{
    totals.buckets+=safeInt(r.buckets);
    totals.torrents+=safeInt(r.rows);
    totals.track_inventory+=safeInt(r.track_inventory);
    totals.aliases+=safeInt(r.aliases);
    totals.sql_reads+=wrapped[i].stats.sql_reads;
    totals.sql_time_ms+=wrapped[i].stats.sql_time_ms;
  });
  // Never reveal the source SHA, URLs, credentials or raw magnet metadata.
  return {
    status:"MULTI_DB_READ_ONLY_STAGING_PASS_NOT_CUTOVER",
    shards:wrapped.length,master_sha256_consistent:true,
    per_shard:results.map((r,i)=>({
      index:r.shard_index,buckets:r.buckets,torrents:r.rows,
      aliases:r.aliases,track_inventory:r.track_inventory,
      fts5:r.sqlite_fts5_query,payload_hash:r.full_payload_sha256_audit,
      routed_buckets_audited:r.routed_buckets_audited,
      sql_reads:wrapped[i].stats.sql_reads,
      elapsed_ms:r.elapsed_ms
    })),
    totals,elapsed_ms:Date.now()-start,
    real_http_golden_parity:"NOT_CLAIMED",
    original_dotnet_live_parity:"NOT_CLAIMED",
    production_cutover_authorized:false
  };
}
