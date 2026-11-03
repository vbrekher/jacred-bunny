// Bunny Edge Scripting standalone entrypoint.
// The deployment bundle must remain server-side: never expose DB env tokens.
import * as BunnySDK from "@bunny.net/edgescript-sdk";
import {connect} from "@bunny.net/database-client";
import {createHandler} from "./src/app.mjs";
import {createGateway} from "./src/gateway.mjs";
import {makeShardProvider} from "./src/shards.mjs";

function readEnv(name){
  if(typeof Deno!=="undefined")return Deno.env.get(name)??"";
  if(typeof process!=="undefined")return process.env[name]??"";
  return "";
}
const env={
  JACRED_EDGE_GATEWAY_ENABLED:readEnv("JACRED_EDGE_GATEWAY_ENABLED"),
  JACRED_WORKER_ORIGIN:readEnv("JACRED_WORKER_ORIGIN"),
  JACRED_EDGE_PREVIEW_ENABLED:readEnv("JACRED_EDGE_PREVIEW_ENABLED"),
  JACRED_EDGE_API_KEY:readEnv("JACRED_EDGE_API_KEY"),
  JACRED_EDGE_ALLOWED_ORIGINS:readEnv("JACRED_EDGE_ALLOWED_ORIGINS")
};
// Secrets never leave the server. Invalid multi-DB configuration fails closed;
 // it must NOT silently fall back to one database and serve partial results.
const shardEnv={JACRED_SQL_SHARD_IDS:readEnv("JACRED_SQL_SHARD_IDS")};
for(let i=0;i<4;i++){
  shardEnv["JACRED_SQL_SHARD_"+i+"_URL"]=readEnv("JACRED_SQL_SHARD_"+i+"_URL");
  shardEnv["JACRED_SQL_SHARD_"+i+"_TOKEN"]=readEnv("JACRED_SQL_SHARD_"+i+"_TOKEN");
}
let client;
function getDb(){
  if(!client)client=makeShardProvider(shardEnv,connect)||connect({timeout:2000});
  return client;
}
const handle=env.JACRED_EDGE_GATEWAY_ENABLED==="true"
  ?createGateway({env})
  :createHandler({getDb,env});
BunnySDK.net.http.serve(handle);
