// Real SQLite source-to-response tests; NOT original .NET golden-response captures.
import test,{before,after} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {createHandler} from "../src/app.mjs";
import {normalizeJacRed,matchesMedia,matchesYear,matchesSeason,filterRows} from "../src/parity.mjs";
import {isReady,searchLegacy} from "../src/sql.mjs";

const BRIDGE=fileURLToPath(new URL("./fixtures/sqlite_bridge.py",import.meta.url));
const SECRET="integration-secret-abcdefghijklmnopqrstuvwxyz";
let tmp,filepath,db,handle;
function python(mode,where,payload=null){
  const child=spawnSync("python3",[BRIDGE,mode,where],{
    encoding:"utf8",input:payload===null?"":JSON.stringify(payload)+"\n",timeout:20000
  });
  if(child.status!==0)throw Error("Python bridge failure: "+child.stderr);
  return JSON.parse(child.stdout.trim());
}
function realSqlite(path){
  return {
    prepare(sql){
      return {
        async first(){return python("query",path,{sql,args:[],mode:"first"});},
        bind(...args){
          return {async all(){return python("query",path,{sql,args,mode:"all"});}};
        }
      };
    }
  };
}
before(()=>{
  tmp=mkdtempSync(join(tmpdir(),"jacred-edge-parity-"));
  const created=python("create",tmp);
  filepath=created.database;
  db=realSqlite(filepath);
  handle=createHandler({getDb:()=>db,env:{
    JACRED_EDGE_PREVIEW_ENABLED:"true",JACRED_EDGE_API_KEY:SECRET
  }});
});
after(()=>{if(tmp)rmSync(tmp,{recursive:true,force:true});});
async function request(path,key=SECRET){
  const r=await handle(new Request("https://isolated.example"+path,{
    headers:{"X-Api-Key":key}
  }));
  const body=await r.text();
  return {status:r.status,body:r.headers.get("content-type")?.includes("json")?
    JSON.parse(body):body,headers:r.headers};
}
test("actual SQLite stage produced by original FileDB gzip-path importer is ready",async()=>{
  assert.equal(await isReady(db),true);
});
test("StringConvert.SearchName semantics: case, Ё/ё, щ and punctuation",()=>{
  assert.equal(normalizeJacRed("ЩЁЛКУНЧИК 2026!"),"шелкунчик2026");
  assert.equal(normalizeJacRed("Шёлковый путь"),"шелковыйпуть");
  assert.equal(normalizeJacRed("A-BC 123"),"abc123");
  assert.equal(normalizeJacRed("їїії"),null); // .NET Regex deliberately strips Ukrainian letters
});
test("query by original RU alias works through actual SQL, not mock",async()=>{
  const r=await request("/api/v2.0/indexers/all/results?query="+encodeURIComponent("Щёлкунчик"));
  assert.equal(r.status,200);
  assert.equal(r.body.Results.length,1);
  assert.equal(r.body.Results[0].Title,"Щёлкунчик & HD 2026");
  assert.equal(r.body.Results[0].Details,"https://tracker.invalid/t/1");
  assert.deepEqual(r.body.Results[0].info.types,["movie"]);
});
test("query by normalized English title original uses bucket alias",async()=>{
  const r=await request("/api/v2.0/indexers/all/results?title_original=The%20Nutcracker");
  assert.equal(r.status,200);
  assert.equal(r.body.Results.length,1);
  assert.equal(r.body.Results[0].Tracker,"rutor");
});
test("substring fallback handles title prefix without leading FTS tokens",async()=>{
  const r=await request("/api/v2.0/indexers/all/results?query="+encodeURIComponent("Шёлковый"));
  assert.equal(r.status,200);
  assert.equal(r.body.Results.length,1);
  assert.equal(r.body.Results[0].Tracker,"rutracker");
});
test("native lower-camel URL comes from FileDB dictionary key if JSON omits url",async()=>{
  const r=await request("/api/v1.0/torrents?search="+encodeURIComponent("Щёлкунчик"));
  assert.equal(r.status,200);
  assert.equal(r.body[0].url,"https://tracker.invalid/t/1");
});
test("media match supports original multfilm, anime, documentary variants",()=>{
  assert.equal(matchesMedia(["anime"],1),true);
  assert.equal(matchesMedia(["anime"],2),true);
  assert.equal(matchesMedia(["documovie"],1),true);
  assert.equal(matchesMedia(["docuserial"],4),true);
  assert.equal(matchesMedia(["serial"],1),false);
});
test("card matching accepts year ±1 for films and ongoing serial years",async()=>{
  const a=await request("/api/v2.0/indexers/all/results?title_original=The%20Nutcracker&year=2025&is_serial=1");
  assert.equal(a.status,200);assert.equal(a.body.Results.length,1);
  const b=await request("/api/v2.0/indexers/all/results?title_original=The%20Nutcracker&year=2023&is_serial=1");
  assert.equal(b.body.Results.length,0);
  assert.equal(matchesYear(2024,2025,false),true);
  assert.equal(matchesYear(2024,2027,false),false);
});
test("S02E03 season/episode matching mirrors original specific title gate",async()=>{
  const a=await request("/torznab/api?t=tvsearch&q="+encodeURIComponent("Сериал")+"&season=2&ep=3");
  assert.equal(a.status,200);
  assert.match(a.body, /Сериал S02E03/);
  assert.doesNotMatch(a.body,/Сериал S02E04/);
  assert.equal(matchesSeason({title:"Series S02E04",seasons:[2]},2,3),false);
  assert.equal(matchesSeason({title:"Series S02",seasons:[2]},2,5),true);
});
test("source tracker filter is binding, not interpolation",async()=>{
  const r=await request("/api/v2.0/indexers/kinozal/results?query="+encodeURIComponent("Щёлкунчик"));
  assert.equal(r.status,200);
  assert.deepEqual(r.body.Results,[]);
});
test("movie-category constraint filters out serials",async()=>{
  const r=await request("/api/v2.0/indexers/all/results?q="+encodeURIComponent("Сериал")+"&cat=2000");
  assert.deepEqual(r.body.Results,[]);
});
test("movie and TV category together preserve mixed results",()=>{
  const input=[{url:"https://example.invalid/1",title:"a",types:["movie"]},
               {url:"https://example.invalid/2",title:"b",types:["serial"]}];
  assert.equal(filterRows(input,{categories:[2000,5000]}).length,2);
});
test("actual SQL source title contains '&' but returned Torznab XML escapes correctly",async()=>{
  const r=await request("/torznab/api?t=search&q="+encodeURIComponent("Щёлкунчик"));
  assert.equal(r.status,200);
  assert.match(r.body, /Щёлкунчик &amp; HD/);
  assert.doesNotMatch(r.body, /Щёлкунчик & HD/);
});
test("bad credentials never reach actual DB",async()=>{
  const r=await request("/api/v1.0/trackers","bad");
  assert.equal(r.status,401);
});
test("all missing canonical source aliases do not create false matches",async()=>{
  const a=await searchLegacy(db,{query:"completelyunknownsearch"});
  assert.deepEqual(a,[]);
});
test("query candidate limit is enforced using real SQLite rows",async()=>{
  await assert.rejects(()=>searchLegacy(db,{query:"Сериал",maxCandidates:1}),
    /candidate safety budget/);
});
