import test from "node:test";
import assert from "node:assert/strict";
import {magnetInfohash,mergeReleases} from "../src/merge.mjs";
const H="0123456789abcdef0123456789abcdef01234567";
const make=(tracker,seed,peers=2,magnet="magnet:?xt=urn:btih:"+H)=>({
 Tracker:tracker,Seeders:seed,Peers:peers,Title:"Same title",MagnetUri:magnet,
 Category:[2000],languages:["ru"],info:{voices:["Дубляж"]},ffprobe:null});
test("40-digit BTIH is canonical case insensitive and other URLs ignored",()=>{
  assert.equal(magnetInfohash("magnet:?xt=urn:btih:"+H.toUpperCase()),H);
  assert.equal(magnetInfohash("https://example.invalid/x"),null);
  assert.equal(magnetInfohash("magnet:?xt=urn:btih:bad"),null);
});
test("different tracker same BTIH merged into one release",()=>{
  const rows=mergeReleases([make("rutor",12),make("kinozal",20)]);
  assert.equal(rows.length,1);
  assert.equal(rows[0].Seeders,20);
});
test("reliable tracker overrules inflated numbers from selezen",()=>{
  const rows=mergeReleases([make("selezen",10000,800),make("rutor",3,2)]);
  assert.equal(rows.length,1);
  assert.equal(rows[0].Seeders,3);
  assert.equal(rows[0].Peers,2);
});
test("korsars is unreliable and does not supplant rutor data",()=>{
  const rows=mergeReleases([make("rutor",5,2),make("korsars",10000,800)]);
  assert.equal(rows[0].Seeders,5);
});
test("merged voices form unique union, languages and ffprobe enriched",()=>{
  const a=make("selezen",40),b=make("rutor",10);
  b.languages=["en"];
  b.info.voices=["Оригинал","Дубляж"];
  b.ffprobe=[{codec_type:"video"}];
  a.languages=[];
  const rows=mergeReleases([a,b]);
  assert.deepEqual(rows[0].info.voices,["Дубляж","Оригинал"]);
  assert.deepEqual(rows[0].languages,["en"]);
  assert.equal(rows[0].ffprobe[0].codec_type,"video");
});
test("does not mutate original cached torrent objects",()=>{
  const a=make("selezen",40);
  const b=make("rutor",10);
  Object.freeze(a.info);Object.freeze(a);Object.freeze(b.info);Object.freeze(b);
  const rows=mergeReleases([a,b]);
  assert.equal(rows[0].Seeders,10);
  assert.equal(a.Seeders,40);
});
test("global sort follows upstream seeders then peers",()=>{
  const a=make("rutor",3,1,"magnet:?xt=urn:btih:"+"a".repeat(40));
  const b=make("kinozal",20,2,"magnet:?xt=urn:btih:"+"b".repeat(40));
  const c=make("rutracker",20,5,"magnet:?xt=urn:btih:"+"c".repeat(40));
  assert.deepEqual(mergeReleases([a,b,c]).map(x=>x.Tracker),["rutracker","kinozal","rutor"]);
});
test("missing magnet fallback does not invent BTIH",()=>{
  const rows=mergeReleases([{...make("rutor",8),MagnetUri:null},
    {...make("kinozal",3),Title:"Different title",MagnetUri:null}]);
  assert.equal(rows.length,2);
});
test("malformed torrent entries are ignored",()=>{
  const rows=mergeReleases([null,undefined,make("rutor",2)]);
  assert.equal(rows.length,1);
});
