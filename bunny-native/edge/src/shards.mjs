/**
 * JacRed Bunny 0.5.2: server-only bounded multi-DB SQL fanout.
 *
 * Every configured shard must be available and share one immutable
 * source/master snapshot. No partial success, client-side routing or
 * automatic database creation. Caller supplies official connect(config).
 */
export const SHARD_KIND="jacred-bunny-sharded-v1";
export const SHARD_LIMIT=4;
export const GLOBAL_CANDIDATE_LIMIT=200;

export class ShardError extends Error {
  constructor(code="SHARD_UNAVAILABLE"){
    super(code);this.name="ShardError";this.code=code;
  }
}
export function shardEnvNames(index){
  if(!Number.isInteger(index)||index<0||index>=SHARD_LIMIT)
    throw new ShardError("INVALID_SHARD_INDEX");
  return {url:"JACRED_SQL_SHARD_"+index+"_URL",
          token:"JACRED_SQL_SHARD_"+index+"_TOKEN"};
}
export function parseShardConfig(env){
  const raw=env.JACRED_SQL_SHARD_IDS;
  if(raw===undefined||raw===null||raw==="")return null;
  if(typeof raw!=="string"||!/^(?:0,1|0,1,2|0,1,2,3)$/.test(raw))
    throw new ShardError("INVALID_SHARD_TOPOLOGY");
  const ids=raw.split(",").map(Number);
  const domains=new Set();
  const descriptors=ids.map(id=>{
    const fields=shardEnvNames(id);
    const url=env[fields.url],token=env[fields.token];
    // Only Bunny Database production endpoints. No authority credentials,
    // path, query, custom ports or unencrypted transport.
    if(typeof url!=="string"||
       !/^libsql:\/\/[a-z0-9][a-z0-9-]*\.lite\.bunnydb\.net$/i.test(url) ||
       typeof token!=="string"||token.length<16||token.length>4096)
      throw new ShardError("INVALID_SHARD_SECRET_OR_ENDPOINT");
    const host=url.toLowerCase();
    if(domains.has(host))throw new ShardError("DUPLICATE_SHARD_DATABASE");
    domains.add(host);
    return {id,url,token};
  });
  return descriptors;
}
export function makeShardProvider(env,connect){
  if(typeof connect!=="function")throw new ShardError("INVALID_CLIENT_FACTORY");
  const config=parseShardConfig(env);
  if(!config)return null;
  const shards=config.map(item=>({
    id:item.id,
    db:connect({url:item.url,authToken:item.token,timeout:2000})
  }));
  return Object.freeze({kind:SHARD_KIND,shards:Object.freeze(shards)});
}
export function isShardProvider(provider){
  return provider?.kind===SHARD_KIND && Array.isArray(provider.shards);
}
async function runAll(provider,operation){
  if(!isShardProvider(provider)||
      provider.shards.length<2||provider.shards.length>SHARD_LIMIT)
    throw new ShardError("INVALID_SHARD_PROVIDER");
  const runs=await Promise.allSettled(provider.shards.map(operation));
  if(runs.some(r=>r.status!=="fulfilled"))
    throw new ShardError("SHARD_UNAVAILABLE");
  return runs.map(r=>r.value);
}
export async function allShardsReady(provider,isSingleReady){
  const responses=await runAll(provider,async shard=>{
    if(!await isSingleReady(shard.db))return null;
    const row=await shard.db.prepare(
      "SELECT shard_index,shard_count,master_sha256 "+
      "FROM native_shard_manifest WHERE id=1"
    ).first();
    return row;
  });
  const n=provider.shards.length;
  let master=null;
  for(let i=0;i<n;i++){
    const row=responses[i];
    if(!row||Number(row.shard_index)!==provider.shards[i].id||
       Number(row.shard_count)!==n||
       typeof row.master_sha256!=="string"||
       !/^[a-f0-9]{64}$/.test(row.master_sha256))return false;
    if(master===null)master=row.master_sha256;
    else if(master!==row.master_sha256)return false;
  }
  return true;
}
function updatedAt(row){
  const ms=Date.parse(row?.updateTime??"");
  return Number.isFinite(ms)?ms:0;
}
export function mergeShardRows(batches,globalLimit=GLOBAL_CANDIDATE_LIMIT){
  if(!Array.isArray(batches)||!Number.isInteger(globalLimit)||globalLimit<1||globalLimit>200)
    throw new ShardError("INVALID_MERGE_INPUT");
  const byUrl=new Map();
  let seen=0;
  for(const rows of batches){
    if(!Array.isArray(rows))throw new ShardError("BAD_SHARD_RESULTS");
    for(const t of rows){
      seen++;
      if(seen>globalLimit)throw new ShardError("GLOBAL_CANDIDATE_LIMIT_EXCEEDED");
      if(!t||typeof t.url!=="string"||!t.url)
        throw new ShardError("SHARD_RESULT_WITHOUT_URL");
      const prior=byUrl.get(t.url);
      if(!prior||updatedAt(t)>updatedAt(prior))
        byUrl.set(t.url,t);
    }
  }
  // Stable ordering across different shard response/connection timing.
  return [...byUrl.values()].sort((a,b)=>a.url.localeCompare(b.url,"en"));
}
export async function searchAllShards(provider,params,singleSearch){
  const n=provider.shards.length;
  if(!Number.isInteger(n)||n<2||n>SHARD_LIMIT)
    throw new ShardError("INVALID_SHARD_PROVIDER");
  const requested=params?.maxCandidates??GLOBAL_CANDIDATE_LIMIT;
  if(!Number.isInteger(requested)||requested<n||requested>GLOBAL_CANDIDATE_LIMIT)
    throw new ShardError("INVALID_GLOBAL_CANDIDATE_BUDGET");
  const size=Math.floor(requested/n),extra=requested%n;
  const batches=await runAll(provider,(shard,i)=>
    singleSearch(shard.db,{...params,maxCandidates:size+(i<extra?1:0)}));
  return mergeShardRows(batches,requested);
}
