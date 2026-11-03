/**
 * JacRed source-derived release merge subset.
 * Source: Infrastructure/Indexers/IndexerResultMerger.cs.
 * Never mutates a shared cache/fileDB result. Exact 40-hex BTIH is supported;
 * other MonoTorrent hash modes remain explicit parity gaps.
 */
const UNRELIABLE=new Set(["selezen","korsars"]);
export function magnetInfohash(uri){
  if(typeof uri!=="string"||!/^magnet:\?/i.test(uri))return null;
  try{
    const params=new URLSearchParams(uri.slice(uri.indexOf("?")+1));
    for(const value of params.getAll("xt")){
      const match=/^urn:btih:([a-f0-9]{40})$/i.exec(value);
      if(match)return match[1].toLowerCase();
    }
  }catch{}
  return null;
}
function reliable(trackers){
  if(typeof trackers!=="string"||!trackers.trim())return true;
  return trackers.split(",").some(x=>!UNRELIABLE.has(x.trim().toLowerCase()));
}
function nonempty(x){return Array.isArray(x)?x:[];}
function mergeArray(one,two){
  return [...new Set([...nonempty(one),...nonempty(two)])];
}
function safeNumber(v){const n=Number(v);return Number.isFinite(n)&&n>0?n:0;}
export function mergeReleases(results){
  if(!Array.isArray(results))throw new TypeError("Expected torrent release array");
  const keys=new Map();
  const reliability=new Map();
  for(const input of results){
    if(!input||typeof input!=="object")continue;
    const hash=magnetInfohash(input.MagnetUri);
    const id=hash?("btih:"+hash):
      ("fallback:"+JSON.stringify([input.Title??null,input.MagnetUri??null]));
    const existing=keys.get(id);
    const current={...input,
      Category:[...nonempty(input.Category)],
      languages:[...nonempty(input.languages)],
      info:input.info?{...input.info,
        voices:[...nonempty(input.info.voices)]}:null};
    if(!existing){
      keys.set(id,current);
      reliability.set(id,reliable(input.Tracker));
      continue;
    }
    const newReliable=reliable(input.Tracker),oldReliable=reliability.get(id);
    if(newReliable&&!oldReliable){
      existing.Seeders=safeNumber(input.Seeders);
      existing.Peers=safeNumber(input.Peers);
      reliability.set(id,true);
    }else if(newReliable===oldReliable && safeNumber(input.Seeders)>safeNumber(existing.Seeders)){
      existing.Seeders=safeNumber(input.Seeders);
      existing.Peers=Math.max(safeNumber(existing.Peers),safeNumber(input.Peers));
    }
    if(current.info?.voices?.length){
      existing.info??={...current.info};
      existing.info.voices=mergeArray(existing.info.voices,current.info.voices);
    }
    if(!existing.ffprobe && input.ffprobe)existing.ffprobe=input.ffprobe;
    if((!existing.languages||!existing.languages.length)&&current.languages.length)
      existing.languages=current.languages;
    if(!existing.info&&current.info)existing.info=current.info;
    // No tracker-name or arbitrary title merging: the upstream merger retains
    // the original representative in this search path.
  }
  const releases=[...keys.values()];
  // Original C# LINQ OrderByDescending(Seeders).ThenByDescending(Peers) is
  // stable for equal counts; order of a sharded source is deterministic first.
  return releases.sort((a,b)=>{
    const seed=safeNumber(b.Seeders)-safeNumber(a.Seeders);
    if(seed!==0)return seed;
    return safeNumber(b.Peers)-safeNumber(a.Peers);
  });
}
