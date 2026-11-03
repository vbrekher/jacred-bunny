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
