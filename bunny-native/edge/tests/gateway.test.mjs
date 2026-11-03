import test from 'node:test';
import assert from 'node:assert/strict';
import {createGateway} from '../src/gateway.mjs';
const env={JACRED_EDGE_GATEWAY_ENABLED:'true',JACRED_WORKER_ORIGIN:'https://mc-abc123.bunny.run'};
const req=(path='/',options={})=>new Request('https://edge.example'+path,options);
test('gateway fails closed for unsafe or absent fixed origin',async()=>{
 for(const origin of ['', 'http://mc-abc.bunny.run','https://evil.test','https://mc-abc.bunny.run.evil.test','https://user:pass@mc-abc.bunny.run','https://mc-abc.bunny.run/path','https://mc-abc.bunny.run?x=1','https://mc-abc.bunny.run:444']){
  let fetched=false;const result=await createGateway({env:{...env,JACRED_WORKER_ORIGIN:origin},fetcher:()=>{fetched=true;}})(req());
  assert.equal(result.status,503);assert.equal(fetched,false);
 }
});
test('preserves original authenticated request and response without injecting authority',async()=>{
 let captured;const handler=createGateway({env,fetcher:async(url,options)=>{captured={url,options};return new Response('{"Results":[1]}',{status:200,headers:{'Content-Type':'application/json','Cache-Control':'public,max-age=999','Set-Cookie':'secret','Server':'internal'}});}});
 const result=await handler(req('/api/v2.0/indexers/all/results?query=Battle&Category%5B%5D=5070',{headers:{'X-Api-Key':'user-api','X-Dev-Key':'user-admin','X-Forwarded-For':'127.0.0.1','Host':'evil.test','Cookie':'private'}}));
 assert.equal(captured.url,'https://mc-abc123.bunny.run/api/v2.0/indexers/all/results?query=Battle&Category%5B%5D=5070');
 assert.equal(captured.options.headers.get('X-Api-Key'),'user-api');assert.equal(captured.options.headers.get('X-Dev-Key'),'user-admin');
 for(const name of ['X-Forwarded-For','Host','Cookie'])assert.equal(captured.options.headers.has(name),false);
 assert.equal(captured.options.redirect,'manual');assert.equal(await result.text(),'{"Results":[1]}');
 assert.match(result.headers.get('Cache-Control'),/no-store/);assert.equal(result.headers.has('Set-Cookie'),false);
});
test('query/path cannot select another authority and anonymous calls stay anonymous',async()=>{
 let actual;const handler=createGateway({env,fetcher:async(url,options)=>{actual=url;assert.equal(options.headers.has('X-Api-Key'),false);assert.equal(options.headers.has('X-Dev-Key'),false);return new Response('denied',{status:401});}});
 const response=await handler(req('//evil.test/admin?url=http://169.254.169.254/'));
 assert.equal(new URL(actual).origin,env.JACRED_WORKER_ORIGIN);assert.equal(response.status,401);assert.equal(await response.text(),'denied');
});
test('preserves original mutation body and administrative denial',async()=>{
 const handler=createGateway({env,fetcher:async(url,options)=>{assert.equal(options.method,'POST');assert.equal(new TextDecoder().decode(options.body),'{}');return new Response('forbidden',{status:403});}});
 assert.equal((await handler(req('/api/v1.0/config',{method:'POST',body:'{}',headers:{'Content-Type':'application/json'}}))).status,403);
});
test('does not follow upstream redirects or expose redirect secrets',async()=>{
 const handler=createGateway({env,fetcher:async()=>new Response('private',{status:302,headers:{Location:'https://evil.test/?key=secret'}})});
 const response=await handler(req());assert.equal(response.status,502);assert.equal(response.headers.has('Location'),false);assert.doesNotMatch(await response.text(),/secret|evil/);
});
test('never caches errors or leaks upstream exceptions',async()=>{
 const handler=createGateway({env,fetcher:async()=>{throw Error('private-token-123');}});
 const response=await handler(req());assert.equal(response.status,502);assert.match(response.headers.get('Cache-Control'),/no-store/);assert.doesNotMatch(await response.text(),/private-token/);
});
test('bounds request and response sizes',async()=>{
 let fetched=false;
 let handler=createGateway({env,fetcher:async()=>{fetched=true;return new Response('ok');}});
 assert.equal((await handler(req('/config',{method:'POST',body:'x'.repeat(2*1024*1024+1)}))).status,413);assert.equal(fetched,false);
 handler=createGateway({env,fetcher:async()=>new Response('x'.repeat(8*1024*1024+1))});
 assert.equal((await handler(req())).status,502);
});
test('deadline includes slow upstream response body',async()=>{
 let cancelled=false;
 const handler=createGateway({env,timeoutMs:10,fetcher:async()=>new Response(new ReadableStream({cancel(){cancelled=true;}}))});
 assert.equal((await handler(req())).status,504);assert.equal(cancelled,true);
});
test('deadline includes slow incoming body',async()=>{
 let fetched=false;const body=new ReadableStream({});
 const handler=createGateway({env,timeoutMs:10,fetcher:async()=>{fetched=true;return new Response('ok');}});
 assert.equal((await handler(req('/config',{method:'POST',body,duplex:'half'}))).status,504);assert.equal(fetched,false);
});
test('HEAD and bodyless statuses remain valid',async()=>{
 for(const status of [200,204,205]){
  const handler=createGateway({env,fetcher:async()=>new Response(null,{status})});
  const response=await handler(req('/',{method:'HEAD'}));assert.equal(response.status,status);assert.equal(await response.text(),'');
 }
});
