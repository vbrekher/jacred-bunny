import {isShardProvider,allShardsReady,searchAllShards} from "./shards.mjs";
// SQL-backed bounded preview. Server-side only, no secrets exposed to clients.
// Official @bunny.net/database-client: prepare(...).bind(...).all()/first().
export function makeMatch(query){
  if(typeof query!=="string"||query.length>180)throw new RangeError("Invalid query");
  const terms=[...query.normalize("NFKC").matchAll(/[\p{L}\p{N}]{2,}/gu)]
    .map(m=>m[0].toLowerCase());
  if(terms.length===0)return null;
  if(terms.length>8||terms.some(t=>t.length>48))throw new RangeError("Query too complex");
  // FTS grammar cannot be injected: only Unicode letters/digits are admitted.
  return terms.map(term=>'"'+term+'"').join(" AND ");
}
export async function isReady(db){
  if(isShardProvider(db))return allShardsReady(db,isReady);
  // v0.4 stage manifest is published ONLY after full import. A partial remote
  // upload must not be mistaken for a complete catalog.
  const row=await db.prepare(
    "SELECT (SELECT bucket_count FROM native_imports WHERE id=1) expected_buckets,"+
    "(SELECT torrent_count FROM native_imports WHERE id=1) expected_torrents,"+
    "(SELECT track_file_count FROM native_imports WHERE id=1) expected_tracks,"+
    "(SELECT COUNT(*) FROM buckets) actual_buckets,"+
    "(SELECT COUNT(*) FROM torrents) actual_torrents,"+
    "(SELECT COUNT(*) FROM track_file_inventory) actual_tracks"
  ).first();
  if(!row)return false;
  return ["buckets","torrents","tracks"].every(k=>
    row["expected_"+k]!==null&&row["expected_"+k]!==undefined&&
    Number(row["expected_"+k])===Number(row["actual_"+k]));
}
export async function searchSql(db,{query,tracker=null,limit=30,offset=0}){
  const match=makeMatch(query);
  if(!match)return [];
  if(!Number.isInteger(limit)||limit<1||limit>100||
     !Number.isInteger(offset)||offset<0||offset>2000)
    throw new RangeError("Invalid pagination");
  const sql="SELECT t.raw_json FROM torrent_fts f JOIN torrents t ON t.id=f.rowid "+
    "WHERE torrent_fts MATCH ?"+
    (tracker?" AND LOWER(t.tracker_name)=?":"")+
    " ORDER BY bm25(torrent_fts),t.bucket_key,t.source_url LIMIT ? OFFSET ?";
  const args=tracker?[match,tracker.toLowerCase(),limit,offset]:[match,limit,offset];
  const rows=await db.prepare(sql).bind(...args).all();
  if(!Array.isArray(rows))throw Error("Unexpected database rows");
  return rows.map(row=>{
    if(typeof row.raw_json!=="string")throw Error("Invalid stored row");
    const data=JSON.parse(row.raw_json);
    if(!data||typeof data!=="object"||Array.isArray(data))
      throw Error("Invalid torrent JSON");
    return data;
  });
}


import {normalizeJacRed} from "./parity.mjs";

// MasterDB/fastdb-alias search: use exact normalized bucket names first,
// then bounded substring fallback. FTS5 alone cannot find compact aliases
// like "щелкунчик" vs "шелкунчик" produced by StringConvert.SearchName.
export async function searchLegacy(db,{query,title=null,titleOriginal=null,
  tracker=null,maxCandidates=200,searchMode="auto"}={}){
  if(isShardProvider(db)){
    if(!await isReady(db))throw new Error("Sharded catalog is incomplete");
    const params={query,title,titleOriginal,tracker,maxCandidates};
    const exact=await searchAllShards(db,{...params,searchMode:"exact"},searchLegacy);
    if(exact.length)return exact;
    return searchAllShards(db,{...params,searchMode:"partial"},searchLegacy);
  }
  if(!["auto","exact","partial"].includes(searchMode))
    throw new RangeError("Invalid search mode");
  if(!Number.isInteger(maxCandidates)||maxCandidates<1||maxCandidates>200)
    throw new RangeError("Candidate budget must be 1..200");
  const input=[title,titleOriginal].filter(Boolean);
  const names=[...new Set((input.length?input:[query])
    .map(normalizeJacRed).filter(Boolean))];
  if(names.length===0)return [];
  if(names.some(n=>n.length>180))throw new RangeError("Query is too long");
  const hasCard=input.length>0;
  async function gather(exact){
    let clause;
    let args;
    if(exact){
      clause="a.alias IN ("+names.map(()=>"?").join(",")+")";
      args=names;
    }else{
      clause="("+names.map(()=>"a.alias LIKE ?").join(" OR ")+")";
      args=names.map(n=>"%"+n+"%");
    }
    const sql="SELECT t.raw_json,t.source_url FROM torrents t WHERE EXISTS ("+
      "SELECT 1 FROM bucket_aliases a WHERE a.bucket_key=t.bucket_key AND "+
      clause+")"+(tracker?" AND LOWER(t.tracker_name)=?":"")+
      " ORDER BY t.bucket_key,t.source_url LIMIT ?";
    if(tracker)args.push(tracker.toLowerCase());
    args.push(maxCandidates+1);
    const rows=await db.prepare(sql).bind(...args).all();
    if(!Array.isArray(rows))throw Error("Unexpected SQL row format");
    if(rows.length>maxCandidates)throw new Error("Query exceeds candidate safety budget");
    return rows.map(row=>{
      if(typeof row.raw_json!=="string")throw Error("Missing payload JSON");
      const body=JSON.parse(row.raw_json);
      if(!body||typeof body!=="object"||Array.isArray(body))
        throw Error("Invalid database payload");
      // Original FileDB is a Dictionary<source_url,TorrentDetails>. Source
      // dictionary key must remain accessible even when JSON omits t.url.
      if(!body.url && typeof row.source_url==="string")body.url=row.source_url;
      if(!body.url)throw Error("Record without URL key");
      return body;
    });
  }
  if(searchMode==="exact")return gather(true);
  if(searchMode==="partial")return gather(false);
  const exact=await gather(true);
  if(exact.length)return exact;
  return gather(false);
}
