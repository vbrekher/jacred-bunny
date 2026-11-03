// Compatibility preview. This is not a claim of JacRed search/ranking parity.
export const CATEGORY_NAMES={2000:"Movies",2010:"Movies/Foreign",5000:"TV",
  5020:"TV/Foreign",5070:"TV/Anime",5080:"TV/Documentary"};
const n=x=>Number.isFinite(Number(x))?Math.max(0,Number(x)):0;
const list=x=>Array.isArray(x)?x.filter(s=>typeof s==="string"):[];
export function categoryIds(types){
  const ids=new Set();
  for(const t of list(types)){
    if(t==="movie")ids.add(2000);
    if(t==="serial")ids.add(5000);
    if(t==="anime")ids.add(5070);
    if(t==="docuserial"||t==="documovie")ids.add(5080);
    if(t==="tvshow"){ids.add(5020);ids.add(2010);}
  }
  return [...ids];
}
export function resultFromTorrent(t){
  const cats=categoryIds(t.types);
  const voices=list(t.voices);
  return {
    Tracker:t.trackerName??null,Details:/^https?:\/\//i.test(t.url??"")?t.url:null,
    Title:t.title??null,Size:n(t.size),PublishDate:t.createTime??"0001-01-01T00:00:00",
    Category:cats,CategoryDesc:cats.length===1?(CATEGORY_NAMES[cats[0]]??null):null,
    Seeders:n(t.sid),Peers:n(t.pir),MagnetUri:t.magnet??null,
    ffprobe:Array.isArray(t.ffprobe)?t.ffprobe:null,languages:list(t.languages),
    info:{name:t.name??null,originalname:t.originalname??null,sizeName:t.sizeName??null,
      relased:n(t.relased),videotype:t.videotype??null,quality:n(t.quality),
      voices,seasons:Array.isArray(t.seasons)?t.seasons:null,types:list(t.types)}
  };
}
const esc=value=>String(value??"").replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&apos;");
export function capsXml(url){
  return '<?xml version="1.0" encoding="UTF-8"?><caps>'+
    '<server version="1.0" title="JacRed" strapline="Edge Preview" url="'+esc(url)+'"/>'+
    '<limits max="100" default="30"/><searching>'+
    '<search available="yes" supportedParams="q"/>'+
    '<tv-search available="yes" supportedParams="q,season,ep"/>'+
    '<movie-search available="yes" supportedParams="q"/>'+
    '</searching><categories><category id="2000" name="Movies"/>'+
    '<category id="5000" name="TV"/><category id="5070" name="TV/Anime"/>'+
    '</categories></caps>';
}
export function indexersXml(names,configured=true){
  return '<?xml version="1.0" encoding="UTF-8"?><indexers>'+
    (configured?["all",...names]:[]).map(x=>'<indexer id="'+esc(x)+
      '" configured="true"><title>'+esc(x==="all"?"JacRed (all trackers)":x)+
      '</title><type>public</type></indexer>').join("")+'</indexers>';
}
function dateOf(v){
  const d=new Date(v??0);
  return Number.isFinite(d.getTime())&&d.getUTCFullYear()>=2000?d:new Date(0);
}
export function rssXml(results,site,api,category=""){
  const entries=results.map(r=>{
    const mag=r.MagnetUri??"";
    const cat=String(category||r.Category[0]||2000).split(",")[0];
    const size=Math.floor(n(r.Size));
    const hash=/btih:([a-f0-9]{40})/i.exec(mag)?.[1]?.toLowerCase()??null;
    const fields={magneturl:mag,size,seeders:r.Seeders,leechers:r.Peers,
      site:r.Tracker,category:cat};
    const attrs=Object.entries(fields).map(([k,v])=>'<torznab:attr name="'+esc(k)+
      '" value="'+esc(v)+'"/>').join("");
    return '<item><title>'+esc(r.Title)+'</title><guid isPermaLink="false">'+
      esc(hash??r.Details??r.Title)+'</guid><link>'+esc(mag)+
      '</link><pubDate>'+esc(dateOf(r.PublishDate).toUTCString())+
      '</pubDate><category>'+esc(cat)+'</category><size>'+size+
      '</size><enclosure url="'+esc(mag)+'" length="'+size+
      '" type="application/x-bittorrent;x-scheme-handler/magnet"/>'+attrs+'</item>';
  }).join("");
  return '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '+
    'xmlns:torznab="http://torznab.com/schemas/2015/feed" '+
    'xmlns:atom="http://www.w3.org/2005/Atom"><channel>'+
    '<atom:link href="'+esc(api)+'" rel="self" type="application/rss+xml"/>'+
    '<title>JacRed</title><description>Edge Preview</description><link>'+esc(site)+
    '</link>'+entries+'</channel></rss>';
}
export function prowlarrRelease(r){
  const date=dateOf(r.PublishDate);
  const age=Math.max(0,Math.floor((Date.now()-date.getTime())/86400000));
  const magnet=r.MagnetUri??null;
  const hash=/btih:([a-f0-9]{40})/i.exec(magnet??"")?.[1]?.toLowerCase()??null;
  return {
    guid:hash??r.Details??r.Title,age,ageHours:age*24,ageMinutes:age*1440,
    size:r.Size,indexerId:1,indexer:r.Tracker??"JacRed (all trackers)",
    title:r.Title,sortTitle:r.Title,publishDate:date.toISOString(),
    downloadUrl:magnet??r.Details,magnetUrl:magnet,infoUrl:r.Details,
    commentUrl:r.Details,
    categories:(r.Category.length?r.Category:[2000]).map(id=>({
      id,name:CATEGORY_NAMES[id]??String(id),subCategories:[]})),
    protocol:"torrent",infoHash:hash,seeders:r.Seeders,leechers:r.Peers,
    ffprobe:r.ffprobe,languages:r.languages,info:r.info
  };
}
