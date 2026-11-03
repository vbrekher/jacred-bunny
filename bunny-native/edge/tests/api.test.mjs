import test from "node:test";
import assert from "node:assert/strict";
import {createHandler} from "../src/app.mjs";
import {TRACKERS} from "../src/catalog.mjs";
import {makeMatch,searchSql} from "../src/sql.mjs";
import {resultFromTorrent,rssXml} from "../src/format.mjs";

const KEY="test-key-abcdefghijklmnopqrstuvwxyz";
const torrent={trackerName:"rutor",url:"https://tracker.example/t/1",
  title:"Фильм & Movie 1080p",name:"Фильм",originalname:"Movie",types:["movie"],
  size:1073741824,quality:1080,createTime:"2026-01-01T00:00:00Z",
  sid:24,pir:3,magnet:"magnet:?xt=urn:btih:1234567890123456789012345678901234567890&tr=test",
  voices:["Дубляж"],languages:["rus","ukr"],seasons:[1],relased:2026,ffprobe:[{codec_type:"audio"}]};
const baseUrl="https://demo.bunny.run";
function db({ready=true,rows=[torrent],fail=false}={}){
  const queries=[];
  return {queries,prepare(sql){
    queries.push(sql);
    if(fail)throw Error("PRIVATE_BUNNY_DATABASE_URL_IS_SECRET");
    return {
      async first(){
        return ready?{expected_buckets:1,expected_torrents:1,expected_tracks:0,
          actual_buckets:1,actual_torrents:1,actual_tracks:0}:null;
      },
      bind(...args){
        queries.push(args);
        return {async all(){
          if(!Array.isArray(args))throw Error("Unbound query");
          const json=rows.filter(r=>!args.some(x=>typeof x==="string" && x==="kinozal"))
            .map(r=>({raw_json:JSON.stringify(r)}));
          return json;
        }};
      }
    };
  }};
}
function harness({enabled=true,ready=true,secret=KEY,rows=[torrent],fail=false,allowed=""}={}){
  const mock=db({ready,rows,fail});
  const handler=createHandler({getDb:()=>mock,env:{
    JACRED_EDGE_PREVIEW_ENABLED:enabled?"true":"false",
    JACRED_EDGE_API_KEY:secret,
    JACRED_EDGE_ALLOWED_ORIGINS:allowed
  }});
  const req=(path,opts={})=>{
    const headers={...(opts.auth===false?{}:{"X-Api-Key":KEY}),...opts.headers};
    return handler(new Request(baseUrl+path,{
      method:opts.method??"GET",headers
    }));
  };
  return {mock,handler,req};
}
async function body(r){return r.json();}
test("25 source names match frozen tracker contract",()=>{
  assert.equal(TRACKERS.length,25);
  assert.equal(new Set(TRACKERS).size,25);
});
test("no preview opt-in => 503 and no database access",async()=>{
  const x=harness({enabled:false});
  const r=await x.req("/api/v1.0/trackers");
  assert.equal(r.status,503);
  assert.equal(x.mock.queries.length,0);
});
test("missing or weak secret fails closed",async()=>{
  assert.equal((await harness({secret:"short"}).req("/health")).status,503);
});
test("wrong API key does not access database",async()=>{
  const x=harness();
  const r=await x.req("/api/v2.0/indexers",{auth:false});
  assert.equal(r.status,401);
  assert.equal(r.headers.get("cache-control"),"private, no-store, max-age=0");
  assert.equal(x.mock.queries.length,0);
});
test("Bearer token can authenticate",async()=>{
  const x=harness();
  const r=await x.req("/api/v1.0/trackers",{auth:false,headers:{Authorization:"Bearer "+KEY}});
  assert.equal(r.status,200);
});
test("query apikey legacy works",async()=>{
  const x=harness();
  assert.equal((await x.req("/api/v1.0/trackers?apikey="+KEY,{auth:false})).status,200);
});
test("configured key introspection does not leak secret",async()=>{
  const x=harness();
  const denied=await body(await x.req("/api/v1.0/conf",{auth:false}));
  assert.equal(denied.apikey,false);
  assert.equal(JSON.stringify(denied).includes(KEY),false);
  const approved=await body(await x.req("/api/v1.0/conf"));
  assert.equal(approved.apikey,true);
});
test("readiness requires complete native_imports manifest",async()=>{
  const x=harness({ready:false});
  assert.equal((await x.req("/api/v1.0/trackers")).status,503);
  assert.equal((await x.req("/health")).status,503);
});
test("health ready is non-sensitive",async()=>{
  const x=harness();
  const r=await x.req("/health",{auth:false});
  assert.equal(r.status,200);
  assert.deepEqual((await body(r)).status,"OK");
});
test("indexer metadata contains 25 source entries and all",async()=>{
  const x=harness();
  const r=await x.req("/api/v2.0/indexers");
  const data=await body(r);
  assert.equal(data.length,26);
  assert.equal(data[0].id,"all");
  assert.equal(data.at(-1).id,"viruseproject");
});
test("tracker endpoint preserves 25 names",async()=>{
  const a=await body(await harness().req("/api/v1.0/trackers"));
  assert.deepEqual(a,TRACKERS);
});
test("JacRed JSON envelope and mixed-case fields",async()=>{
  const r=await harness().req("/api/v2.0/indexers/all/results?query=Movie");
  const payload=await body(r);
  assert.equal(payload.jacred,true);
  assert.equal(payload.Results.length,1);
  assert.equal(payload.Results[0].Tracker,"rutor");
  assert.equal(payload.Results[0].Seeders,24);
  assert.deepEqual(payload.Results[0].Category,[2000]);
  assert.equal(payload.Results[0].info.quality,1080);
  assert.equal(payload.Results[0].ffprobe[0].codec_type,"audio");
});
test("empty search is empty results, not fabricated matches",async()=>{
  const r=await harness().req("/api/v2.0/indexers/all/results");
  assert.deepEqual(await body(r),{Results:[],jacred:true});
});
test("tracker filter is bounded by known registry",async()=>{
  const x=harness();
  const bad=await x.req("/api/v2.0/indexers/unknown/results?query=Film");
  assert.equal(bad.status,400);
});
test("tracker known nonmatching filter binds safe SQL",async()=>{
  const x=harness();
  const r=await x.req("/api/v2.0/indexers/kinozal/results?query=Film");
  assert.equal((await body(r)).Results.length,0);
  assert.match(x.mock.queries.filter(x=>typeof x==="string").at(-1),/LOWER\(t\.tracker_name\)=\?/);
});
test("filter serial type excludes movie torrents",async()=>{
  const r=await harness().req("/api/v2.0/indexers/all/results?q=Movie&is_serial=2");
  assert.deepEqual((await body(r)).Results,[]);
});
test("Torznab caps format and content type",async()=>{
  const r=await harness().req("/torznab/api?t=caps");
  assert.equal(r.status,200);
  assert.match(r.headers.get("content-type"),/application\/xml/);
  assert.match(await r.text(),/<caps>/);
});
test("Torznab indexers lists the 25 sources",async()=>{
  const r=await harness().req("/torznab/api?t=indexers");
  const xml=await r.text();
  assert.match(xml,/<indexer id="rutor"/);
  assert.match(xml,/<indexer id="all"/);
});
test("Torznab RSS escapes title and magnet",async()=>{
  const r=await harness().req("/torznab/api?t=search&q=Movie");
  const xml=await r.text();
  assert.match(xml,/<rss /);
  assert.match(xml,/Фильм &amp; Movie/);
  assert.match(xml,/urn:btih:1234567890123456789012345678901234567890&amp;tr=/);
});
test("torznab alternate native paths work",async()=>{
  const r=await harness().req("/api/v2.0/indexers/rutor/results/torznab/api?t=caps");
  assert.equal(r.status,200);
});
test("Prowlarr discovery matches original aggregate indexer ID",async()=>{
  const x=harness();
  const list=await body(await x.req("/api/v1/indexer"));
  assert.equal(list[0].id,1);
  assert.equal((await body(await x.req("/api/v1/indexer/1"))).implementation,"Torznab");
  assert.equal((await x.req("/api/v1/indexer/2")).status,404);
});
test("Prowlarr search JSON contains required compatibility fields",async()=>{
  const a=await body(await harness().req("/api/v1/search?query=Movie"));
  assert.equal(a.length,1);
  assert.equal(a[0].indexerId,1);
  assert.equal(a[0].protocol,"torrent");
  assert.equal(a[0].seeders,24);
  assert.equal(a[0].categories[0].id,2000);
});
test("Prowlarr usenet-only selection returns no torrent results",async()=>{
  const a=await body(await harness().req("/api/v1/search?query=Movie&indexerIds=-1"));
  assert.deepEqual(a,[]);
});
test("native Torrent endpoint retains existing lower camel fields",async()=>{
  const a=await body(await harness().req("/api/v1.0/torrents?search=Movie"));
  assert.equal(a[0].tracker,"rutor");
  assert.equal(a[0].quality,1080);
  assert.equal(a[0].magnet,torrent.magnet);
});
test("unfinished quality API gives 501, never false empty success",async()=>{
  const r=await harness().req("/api/v1.0/qualitys?name=Movie");
  assert.equal(r.status,501);
  assert.equal((await body(r)).code,"NOT_IMPLEMENTED");
});
test("unknown route returns 404",async()=>{
  assert.equal((await harness().req("/unknown")).status,404);
});
test("non-GET requests do not mutate data",async()=>{
  const x=harness();
  assert.equal((await x.req("/api/v2.0/indexers",{method:"POST"})).status,405);
  assert.equal(x.mock.queries.length,0);
});
test("CORS disallows unsanctioned Origin before auth/database",async()=>{
  const x=harness({allowed:"https://allowed.example"});
  const r=await x.req("/api/v1.0/trackers",{headers:{Origin:"https://evil.example"}});
  assert.equal(r.status,403);
  assert.equal(x.mock.queries.length,0);
});
test("CORS allowlist exact origin only",async()=>{
  const r=await harness({allowed:"https://allowed.example"}).req("/api/v1.0/trackers",
    {headers:{Origin:"https://allowed.example"}});
  assert.equal(r.headers.get("access-control-allow-origin"),"https://allowed.example");
});
test("invalid oversized query fails without SQL FTS injection",async()=>{
  const x=harness();
  const r=await x.req("/api/v2.0/indexers/all/results?q="+("a".repeat(181)));
  assert.equal(r.status,400);
});
test("FTS5 grammar operators are never interpolated from user input",()=>{
  const v=makeMatch('Movie") OR DROP TABLE torrents;');
  assert.equal(v,'"movie" AND "or" AND "drop" AND "table" AND "torrents"');
  assert.throws(()=>makeMatch("a".repeat(200)),RangeError);
});
test("SQL uses bound params and LIMIT",async()=>{
  const seen=[];
  const db={prepare(sql){seen.push(sql);return {bind(...args){seen.push(args);return {async all(){return [];}}}}}};
  await searchSql(db,{query:"фильм Movie",limit:17,offset:2});
  assert.equal(seen[1][0],'"фильм" AND "movie"');
  assert.deepEqual(seen[1].slice(-2),[17,2]);
  assert.match(seen[0],/LIMIT \? OFFSET \?/);
});
test("backend exception does not expose credentials or SQL",async()=>{
  const r=await harness({fail:true}).req("/api/v2.0/indexers");
  assert.equal(r.status,503);
  assert.doesNotMatch(await r.text(),/PRIVATE_BUNNY_DATABASE_URL_IS_SECRET/);
});
test("HTML special characters must escape in Torznab XML",()=>{
  const xml=rssXml([resultFromTorrent(torrent)],"https://domain.example","https://domain.example/torznab");
  assert.match(xml,/&amp; Movie/);
  assert.doesNotMatch(xml,/<title>Фильм & Movie/);
});

test("HEAD requests carry no body with correct status and security headers",async()=>{
  const r=await harness().req("/api/v2.0/indexers",{method:"HEAD"});
  assert.equal(r.status,200);
  assert.equal(await r.text(),"");
  assert.equal(r.headers.get("cache-control"),"private, no-store, max-age=0");
});
