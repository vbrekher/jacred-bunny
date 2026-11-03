// JacRed Bunny Native v0.5.0: request-bounded, authenticated Edge API PREVIEW.
// No app/DB deployment or production cutover occurs from importing this file.
import {TRACKERS,TRACKER_SET} from "./catalog.mjs";
import {resultFromTorrent,rssXml,capsXml,indexersXml,prowlarrRelease} from "./format.mjs";
import {searchSql,isReady} from "./sql.mjs";

const VERSION="0.5.0-edge-preview";
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
function filtered(data,q){
  const year=getParam(q,"year");
  const cat=getParam(q,"cat","category");
  const typ=getParam(q,"type");
  const serial=getParam(q,"is_serial");
  const cats=cat.split(",").filter(Boolean);
  return data.filter(t=>{
    const types=Array.isArray(t.types)?t.types:[];
    if(typ&&!types.includes(typ))return false;
    if(serial==="1"&&!types.includes("movie"))return false;
    if(serial==="2"&&!types.includes("serial"))return false;
    if(year&&String(t.relased??"")!==year)return false;
    if(cats.length){
      const result=resultFromTorrent(t);
      if(!result.Category.some(c=>cats.some(cat=>String(c).startsWith(cat))))return false;
    }
    return true;
  });
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
        const rows=filtered(await searchSql(db,{query:phrase,tracker,limit,offset}),q);
        const payload=rssXml(rows.map(resultFromTorrent),originUrl,apiUrl,getParam(q,"cat"));
        return attach(xml(payload),responseHeaders);
      }
      const jackett=pathname.match(/^\/api\/v2\.0\/indexers\/([a-z0-9_-]+)\/results$/i);
      if(jackett){
        const tracker=requestedTracker(q,jackett[1]);
        const phrase=requestQuery(q);
        if(!phrase)return attach(json({Results:[],jacred:true}),responseHeaders);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchSql(db,{query:phrase,tracker,limit,offset}),q);
        return attach(json({Results:rows.map(resultFromTorrent),jacred:true}),responseHeaders);
      }
      if(pathname==="/api/v1/search"){
        const ids=q.getAll("indexerIds").flatMap(x=>x.split(","));
        if(ids.length && !ids.includes("1")&&!ids.includes("-2"))
          return attach(json([]),responseHeaders);
        const phrase=requestQuery(q);
        if(!phrase)return attach(json([]),responseHeaders);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchSql(db,{query:phrase,limit,offset}),q);
        return attach(json(rows.map(resultFromTorrent).map(prowlarrRelease)),responseHeaders);
      }
      if(pathname==="/api/v1.0/torrents"){
        const phrase=getParam(q,"search","altname");
        if(!phrase)return attach(json([]),responseHeaders);
        const tracker=requestedTracker(q,null);
        const limit=pageNum(q.get("limit"),30,1,100);
        const offset=pageNum(q.get("offset"),0,0,2000);
        const rows=filtered(await searchSql(db,{query:phrase,tracker,limit,offset}),q);
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
