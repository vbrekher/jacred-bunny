// JacRed v0.5.1: offline-verified subset of original C# matching semantics.
// Source of truth: StringConvert.SearchName, JackettCardMatcher,
// IndexerResultFilters.MatchesCardYear and SeasonEpisodeFilter.
// A subset is not a full runtime golden-response certification.

export function normalizeJacRed(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const s=value.toLowerCase().replace(/[^a-zа-я0-9ё]/g, "")
    .replace(/ё/g,"е").replace(/щ/g,"ш");
  return s || null;
}
export function matchesMedia(types, serial) {
  if (!Number.isInteger(serial) || serial === -1) return true;
  if (!Array.isArray(types) || types.length===0) return false;
  const sets={
    1:["movie","multfilm","anime","documovie"],
    2:["serial","multserial","anime","docuserial","tvshow"],
    3:["tvshow"],
    4:["documovie","docuserial"],
    5:["anime"]
  };
  if (!Object.hasOwn(sets,serial)) return true;
  return sets[serial].some(k=>types.includes(k));
}
export function matchesYear(released,year,movieLike=true){
  if(!Number.isInteger(year)||year<=0||Number(released)<=0)return true;
  const r=Number(released);
  return movieLike?Math.abs(r-year)<=1:r>=year-1;
}
export function matchesCard(t,{title=null,titleOriginal=null,serial=-1,year=0}={}){
  if (!t || !Array.isArray(t.types) || !t.title || t.title.includes(" КПК"))
    return false;
  const normTitle=normalizeJacRed(title),normOriginal=normalizeJacRed(titleOriginal);
  const name=t._sn??normalizeJacRed(t.name);
  const original=t._so??normalizeJacRed(t.originalname);
  if(normTitle||normOriginal){
    if((!normTitle||normTitle!==name)&&(!normOriginal||normOriginal!==original))
      return false;
  }
  if(!matchesMedia(t.types,serial))return false;
  const movieLike=serial===1 || (serial!==2&&serial!==3&&serial!==4&&serial!==5&&
    ["movie","multfilm","documovie"].some(s=>t.types.includes(s)));
  if(!matchesYear(t.relased,year,movieLike))return false;
  if(serial===1 && / (сезон|сери(и|я|й))/i.test(t.title))return false;
  return true;
}
const PATTERNS=[
  /(?<!\d)s(?<s>\d{1,2})[\s._-]*e(?<e>\d{1,3})(?!\d)/i,
  /(?<!\d)(?<s>\d{1,2})x(?<e>\d{1,3})(?!\d)/i,
  /(?<!\d)s(?<s>\d{1,2})(?!\d|\s*e)(?:\s|\.|\]|\/|$|[[(])/i
];
export function parseSeason(title){
  if(typeof title!=="string")return null;
  for(let i=0;i<PATTERNS.length;i++){
    const m=title.match(PATTERNS[i]);
    if(m)return {season:Number(m.groups.s),episode:m.groups.e?Number(m.groups.e):null,
      isPack:i===2};
  }
  return null;
}
export function matchesSeason(t,season,episode=null){
  if(!Number.isInteger(season)||season<=0)return true;
  const listed=Array.isArray(t.seasons)?t.seasons:[];
  const parsed=parseSeason(t.title);
  if(listed.length>0){
    if(!listed.includes(season))return false;
    if(!Number.isInteger(episode)||episode<=0||!parsed)return true;
    return parsed.season===season&&(parsed.episode===null||parsed.episode===episode);
  }
  if(!parsed||parsed.season!==season)return false;
  if(!Number.isInteger(episode)||episode<=0)return true;
  return parsed.isPack||parsed.episode===episode;
}
export function filterRows(torrents,{title=null,titleOriginal=null,serial=-1,
  year=0,season=0,episode=0,categories=[],tracker=null,type=null,
  exact=false,query=null}={}){
  const normalized=normalizeJacRed(query);
  const seen=new Map();
  for(const t of torrents){
    if(!t||!Array.isArray(t.types)||!t.title||t.title.includes(" КПК"))continue;
    if(title||titleOriginal){
      if(!matchesCard(t,{title,titleOriginal,serial,year}))continue;
    }else{
      if(!matchesMedia(t.types,serial))continue;
      if(!matchesYear(t.relased,year,true))continue;
      if(exact&&normalized){
        if((t._sn??normalizeJacRed(t.name))!==normalized &&
           (t._so??normalizeJacRed(t.originalname))!==normalized)continue;
      }
    }
    if(tracker&&String(t.trackerName??"").toLowerCase()!==tracker.toLowerCase())continue;
    if(type&&!t.types.includes(type))continue;
    if(!matchesSeason(t,season,episode))continue;
    if(Array.isArray(categories)&&categories.length){
      const kinds=new Set();
      for(const x of t.types){
        if(["movie","multfilm","documovie"].includes(x))kinds.add(2000);
        if(["serial","multserial","docuserial","tvshow","anime"].includes(x))kinds.add(5000);
      }
      const movie=categories.some(x=>x>=2000&&x<3000);
      const tv=categories.some(x=>x>=5000&&x<6000);
      if(!(movie&&tv)&&kinds.size&&!categories.some(x=>kinds.has(Math.floor(x/1000)*1000)))continue;
    }
    const id=t.url??t._source_url;
    if(typeof id!=="string"||!id)continue;
    const prev=seen.get(id);
    if(!prev||new Date(t.updateTime??0).getTime()>new Date(prev.updateTime??0).getTime())
      seen.set(id,t);
  }
  return [...seen.values()];
}
