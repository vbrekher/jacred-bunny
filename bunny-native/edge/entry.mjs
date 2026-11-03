// Bunny Edge Scripting standalone entrypoint.
// The deployment bundle must remain server-side: never expose DB env tokens.
import * as BunnySDK from "@bunny.net/edgescript-sdk";
import {connect} from "@bunny.net/database-client";
import {createHandler} from "./src/app.mjs";

function readEnv(name){
  if(typeof Deno!=="undefined")return Deno.env.get(name)??"";
  if(typeof process!=="undefined")return process.env[name]??"";
  return "";
}
const env={
  JACRED_EDGE_PREVIEW_ENABLED:readEnv("JACRED_EDGE_PREVIEW_ENABLED"),
  JACRED_EDGE_API_KEY:readEnv("JACRED_EDGE_API_KEY"),
  JACRED_EDGE_ALLOWED_ORIGINS:readEnv("JACRED_EDGE_ALLOWED_ORIGINS")
};
let client;
function getDb(){
  if(!client)client=connect({timeout:2000});
  return client;
}
const handle=createHandler({getDb,env});
BunnySDK.net.http.serve(handle);
