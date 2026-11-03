// JacRed Bunny Native v0.5.0: request-bounded, authenticated Edge API PREVIEW.
// No app/DB deployment or production cutover occurs from importing this file.
import {TRACKERS,TRACKER_SET} from "./catalog.mjs";
import {resultFromTorrent,rssXml,capsXml,indexersXml,prowlarrRelease} from "./format.mjs";
import {searchLegacy,isReady} from "./sql.mjs";
import {filterRows} from "./parity.mjs";
import {mergeReleases} from "./merge.mjs";

const VERSION="0.5.2-sharded-search-preview";
const API_ORIGIN="https://github.com/vbrekher/jacred-bunny";
const BAD={error:"Edge preview unavailable",productionReady:false};
function json(body,status=200,headers={}){
  return new Response(JSON.stringify(body),{
    status,headers:{"Content-Type":"application/json; charset=utf-8",...headers}});
}
function xml(body,status=200,headers={}){
  return new Response(body,{status,headers:{"Content-Type":"application/xml; charset=utf-8",...headers}});
}
function deny(status=401,why="Unauthorized"){
  return json({error:why},status,status===401?{"WWW-Authenticate":'Bearer realm="JacRed Bunny Preview"'}:{});
}
function goodKey(a,b){
  if(typeof a!=="string"||typeof b!=="string"||a.length>256||b.length>256)return false;
  // Avoid early-exit byte comparison (JS does not promise formal constant time).
  const left=new TextEncoder().encode(a),right=new TextEncoder().encode(b);
  let different=left.length^right.length;
  for(let i=0;i<256;i++)different|=(left[i]??0)^(right[i]??0);
  return different===0&&a.length>=16;
}
function clientKey(request,params){
  const authorization=request.headers.get("authorization")??"";
  if(authorization.toLowerCase().startsWith("bearer "))return authorization.slice(7).trim();
  return request.headers.get("x-api-key")??params.get("apikey")??params.get("api_key")??"";
}
function getParam(q,...keys){
  for(const name of keys){const v=q.get(name);if(v&&v.trim())return v.trim();}
  return "";
}
function pageNum(v,fallback,min,max){
  if(v==null||v==="")return fallback;
  if(!/^\d{1,5}$/.test(v))throw new RangeError("Invalid pagination");
  const n=Number(v);if(n<min||n>max)throw new RangeError("Invalid pagination");
  return n;
}
function requestQuery(q){
  return getParam(q,"query","q","Query","title","title_original","search");
}
function requestedTracker(q,fromPath){
  const tracker=fromPath&&fromPath!=="all"?fromPath.toLowerCase():getParam(q,"tracker","Tracker").toLowerCase();
  if(!tracker)return null;
  if(!TRACKER_SET.has(tracker))throw new RangeError("Unknown tracker");
  return tracker;
}
function filtered(data,q,{phrase=""}={}){
  const cats=q.getAll("cat").concat(q.getAll("category"))
    .concat([...q.entries()].filter(([k])=>/^category\[\d+\]$/i.test(k)).map(([,v])=>v))
    .flatMap(x=>x.split(",")).map(Number).filter(Number.isInteger);
  const title=getParam(q,"title");
  const titleOriginal=getParam(q,"title_original");
  const serialText=getParam(q,"is_serial");
  const yearValue=getParam(q,"year");
  const seasonValue=getParam(q,"season");
  const episodeValue=getParam(q,"ep","episode");
  const parse=(v,min,max,defaultVal)=>{
    if(!v)return defaultVal;
    if(!/^\d{1,5}$/.test(v))throw new RangeError("Invalid numeric filter");
    const n=Number(v);
    if(n<min||n>max)throw new RangeError("Invalid numeric filter");
    return n;
  };
  let serial=serialText?parse(serialText,0,5,-1):-1;
  if(serial===0&&cats.length){
    if(cats.includes(5020)||cats.includes(2010))serial=3;
    else if(cats.includes(5080))serial=4;
    else if(cats.includes(5070))serial=5;
    else if(cats.some(x=>x>=2000&&x<3000))serial=1;
    else if(cats.some(x=>x>=5000&&x<6000))serial=2;
  }
  const year=parse(yearValue,1,9999,0);
  const season=parse(seasonValue,1,999,0);
  const episode=parse(episodeValue,1,999,0);
  let rows=filterRows(data,{query:phrase,title,titleOriginal,serial,year,season,episode,
    categories:cats,type:getParam(q,"type")||null});
  // The v1 JSON API exposes additional filters not part of Jackett card mode.
  const quality=getParam(q,"quality");
  const video=getParam(q,"videotype");
  const voice=getParam(q,"voice");
  const release=getParam(q,"relased");
  if(quality)rows=rows.filter(t=>Number(t.quality)===parse(quality,1,9999,0));
  if(video)rows=rows.filter(t=>t.videotype===video);
  if(release)rows=rows.filter(t=>Number(t.relased)===parse(release,1,9999,0));
  if(voice)rows=rows.filter(t=>Array.isArray(t.voices)&&t.voices.includes(voice));
  const sort=getParam(q,"sort");
  const numeric={sid:"sid",pir:"pir",size:"size"};
  const sortBy=numeric[sort];
  if(sortBy)rows.sort((a,b)=>Number(b[sortBy]??0)-Number(a[sortBy]??0));
  return rows;
}
function metadata(){
  return [{id:"all",name:"JacRed (all trackers)",
    description:"Aggregated JacRed search across configured trackers",type:"public",
    configured:true,link:API_ORIGIN},...TRACKERS.map(t=>({
      id:t,name:t,description:"JacRed tracker: "+t,type:"public",configured:true,link:API_ORIGIN
    }))];
}
function indexer(){
  return {id:1,name:"JacRed (all trackers)",
    description:"Aggregated JacRed search across configured trackers",
    implementation:"Torznab",implementationName:"Torznab",enable:true,
    protocol:"torrent"};
}
function legacyTorrent(t){
  return {tracker:t.trackerName??null,
    url:/^https?:\/\//i.test(t.url??"")?t.url:null,
    title:t.title??null,size:Number(t.size??0),sizeName:t.sizeName??null,
    createTime:t.createTime??null,updateTime:t.updateTime??null,
    sid:Number(t.sid??0),pir:Number(t.pir??0),magnet:t.magnet??null,
    name:t.name??null,originalname:t.originalname??null,relased:Number(t.relased??0),
    videotype:t.videotype??null,quality:Number(t.quality??0),
    voices:t.voices??[],seasons:t.seasons??[],types:t.types??[]};
}
export function createHandler({getDb,env={}}){
  if(typeof getDb!=="function")throw Error("getDb must be a function");
  const enabled=env.JACRED_EDGE_PREVIEW_ENABLED==="true";
  const secret=env.JACRED_EDGE_API_KEY??"";
  const origins=(env.JACRED_EDGE_ALLOWED_ORIGINS??"").split(",").map(x=>x.trim())
    .filter(s=>/^https:\/\/[^/]+$/i.test(s));
  const respond=async function handleRequest(request){
    const url=new URL(request.url);
    const q=url.searchParams;
    const pathname=url.pathname.replace(/\/+$/,"")||"/";
    const responseHeaders={"Cache-Control":"private, no-store, max-age=0",
      "Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
      "X-JacRed-Compatibility":"preview-not-parity-certified"};
    const origin=request.headers.get("Origin");
    if(origin){
      if(!origins.includes(origin))
        return attach(deny(403,"Origin not allowed"),responseHeaders);
      responseHeaders["Access-Control-Allow-Origin"]=origin;
      responseHeaders["Vary"]="Origin";
      responseHeaders["Access-Control-Allow-Headers"]="Authorization, X-Api-Key, Content-Type";
      responseHeaders["Access-Control-Allow-Methods"]="GET, HEAD, OPTIONS";
    }
    if(request.method==="OPTIONS")
      return attach(new Response(null,{status:204}),responseHeaders);
    if(request.method!=="GET"&&request.method!=="HEAD")
      return attach(json({error:"Read-only preview",code:"METHOD_NOT_ALLOWED"},405,
        {"Allow":"GET, HEAD, OPTIONS"}),responseHeaders);
    if(url.toString().length>2048||[...q.keys()].length>24)
      return attach(json({error:"Request too large"},414),responseHeaders);
    if(!enabled||secret.length<16)
      return attach(json(BAD,503),responseHeaders);
    // /health is safe without a credential; do not reveal counts or DB URL.
    if(pathname==="/health"){
      try{
        const ready=await isReady(await getDb());
        return attach(json(ready?{status:"OK",version:VERSION,mode:"preview"}:
          {status:"NOT_READY",version:VERSION},ready?200:503),responseHeaders);
      }catch{
        return attach(json({status:"NOT_READY",version:VERSION},503),responseHeaders);
      }
    }
    const authenticated=goodKey(clientKey(request,q),secret);
    if(pathname==="/api/v1.0/conf")
      return attach(json({jacred:true,configured:true,apikey:authenticated,version:VERSION}),responseHeaders);
    if(!authenticated)return attach(deny(),responseHeaders);
    try{
      const db=await getDb();
      if(!await isReady(db))return attach(json({error:"Catalog manifest incomplete",
        code:"DATASET_NOT_READY"},503),responseHeaders);

      if(pathname==="/api/v1.0/trackers")return attach(json(TRACKERS),responseHeaders);
      if(pathname==="/api/v2.0/indexers")return attach(json(metadata()),responseHeaders);
      if(pathname==="/api/v1/indexer")return attach(json([indexer()]),responseHeaders);
      if(/^\/api\/v1\/indexer\/\d+$/.test(pathname)){
        if(pathname!=="/api/v1/indexer/1")return attach(deny(404,"Not found"),responseHeaders);
        return attach(json({...indexer(),fields:[]}),responseHeaders);
      }
      if(pathname==="/api/v1.0/qualitys"||pathname.startsWith("/api/v1.0/config")||
         pathname.startsWith("/sync/")||pathname.startsWith("/cron/"))
        return attach(json({error:"Not ported in Edge preview",
          code:"NOT_IMPLEMENTED",fallback:"Original .NET JacRed"},501),responseHeaders);

      const torzMatch=pathname.match(/^\/api\/v2\.0\/indexers\/([a-z0-9_-]+)\/results\/torznab\/api$/i);
      const newznabMatch=pathname.match(/^\/api\/v1\/indexer\/([a-z0-9_-]+)\/newznab$/i);
      const torz=pathname==="/torznab/api"||torzMatch||newznabMatch;
      if(torz){
        const pathTracker=torzMatch?.[1]??newznabMatch?.[1]??"all";
        const tracker=requestedTracker(q,pathTracker);
        const typ=getParam(q,"t").toLowerCase();
        const originUrl=url.origin;
        const apiUrl=url.origin+pathname;
        if(typ==="caps")return attach(xml(capsXml(apiUrl)),responseHeaders);
        if(typ==="indexers")return attach(xml(indexersXml(TRACKERS,
          getParam(q,"configured")!=="false")),responseHeaders);
        if(typ&&!["search","tvsearch","movie","moviesearch","tv"].includes(typ))
          return attach(json({error:"Unsupported Torznab action"},400),responseHeaders);
        const phrase=requestQuery(q);
        if(!phrase)return attach(xml(rssXml([],originUrl,apiUrl)),responseHeaders);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchLegacy(db,{query:phrase,title:getParam(q,"title"),
          titleOriginal:getParam(q,"title_original"),tracker}),q,{phrase}).slice(offset,offset+limit);
        const payload=rssXml(mergeReleases(rows.map(resultFromTorrent)),originUrl,apiUrl,getParam(q,"cat"));
        return attach(xml(payload),responseHeaders);
      }
      const jackett=pathname.match(/^\/api\/v2\.0\/indexers\/([a-z0-9_-]+)\/results$/i);
      if(jackett){
        const tracker=requestedTracker(q,jackett[1]);
        const phrase=requestQuery(q);
        if(!phrase)return attach(json({Results:[],jacred:true}),responseHeaders);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchLegacy(db,{query:phrase,title:getParam(q,"title"),
          titleOriginal:getParam(q,"title_original"),tracker}),q,{phrase}).slice(offset,offset+limit);
        return attach(json({Results:mergeReleases(rows.map(resultFromTorrent)),jacred:true}),responseHeaders);
      }
      if(pathname==="/api/v1/search"){
        const ids=q.getAll("indexerIds").flatMap(x=>x.split(","));
        if(ids.length && !ids.includes("1")&&!ids.includes("-2"))
          return attach(json([]),responseHeaders);
        const phrase=requestQuery(q);
        if(!phrase)return attach(json([]),responseHeaders);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchLegacy(db,{query:phrase,title:getParam(q,"title"),
          titleOriginal:getParam(q,"title_original")}),q,{phrase}).slice(offset,offset+limit);
        return attach(json(mergeReleases(rows.map(resultFromTorrent)).map(prowlarrRelease)),responseHeaders);
      }
      if(pathname==="/api/v1.0/torrents"){
        const phrase=getParam(q,"search","altname");
        if(!phrase)return attach(json([]),responseHeaders);
        const tracker=requestedTracker(q,null);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchLegacy(db,{query:phrase,title:getParam(q,"title"),
          titleOriginal:getParam(q,"title_original"),tracker}),q,{phrase}).slice(offset,offset+limit);
        return attach(json(rows.map(legacyTorrent)),responseHeaders);
      }
      return attach(deny(404,"Not found"),responseHeaders);
    }catch(error){
      if(error instanceof RangeError)
        return attach(json({error:error.message,code:"INVALID_REQUEST"},400),responseHeaders);
      // No SQL, connection string, tracker URLs or tokens in error responses.
      return attach(json({error:"Data backend unavailable",code:"BACKEND_ERROR"},503),responseHeaders);
    }
  };
  return async function handle(request){
    const response=await respond(request);
    if(request.method==="HEAD")
      return new Response(null,{status:response.status,headers:response.headers});
    return response;
  };
}
function attach(response,headers){
  const out=new Response(response.body,{status:response.status,headers:response.headers});
  for(const [k,v] of Object.entries(headers))out.headers.set(k,v);
  return out;
}
