#!/usr/bin/env python3
"""Explicit opt-in private HTTP golden capture; no requests without --run."""
import argparse,json,os,re,sys,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError

class Refused(Exception):pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def validate(base,cases):
    url=urlsplit(base)
    if url.scheme!="https" and not (url.scheme=="http" and url.hostname in ("localhost","127.0.0.1","::1")):
        raise Refused()
    if not url.hostname or url.username or url.password or url.path not in ("","/") or url.query or url.fragment:
        raise Refused()
    data=json.loads(Path(cases).read_text("utf-8"))
    if data.get("schema")!="jacred.bunny.http-compare.v1" or not 1<=len(data.get("cases",[]))<=100:
        raise Refused()
    for c in data["cases"]:
        path=c.get("path","")
        if not re.fullmatch("[a-zA-Z0-9_-]{1,60}",c.get("id","")) or c.get("type") not in ("json","xml"):
            raise Refused()
        if not path.startswith("/") or path.startswith("//") or len(path)>512:
            raise Refused()
        if any(x in path.lower() for x in ("apikey=","passkey=","token=","magnet:")):
            raise Refused()
    if len({c["id"] for c in data["cases"]})!=len(data["cases"]):raise Refused()
    return data

def capture(base,cases,token):
    opener=build_opener(NoRedirect())
    rows=[]
    for case in cases:
        request=Request(base.rstrip("/")+case["path"],headers={
            "X-Api-Key":token,"Cache-Control":"no-store","Accept":"application/json, application/xml"},
            method="GET")
        try:result=opener.open(request,timeout=15)
        except HTTPError as e:result=e
        except (URLError,OSError) as e:raise Refused() from e
        with result:
            raw=result.read(10_000_001)
            if len(raw)>10_000_000:raise Refused()
            rows.append({"id":case["id"],"path":case["path"],"type":case["type"],
                         "status":result.status,"body":raw.decode("utf-8-sig")})
    return {"schema":"jacred.bunny.http-compare.v1","cases":rows}

def private_output(path,result):
    target=Path(path)
    if target.is_symlink() or target.exists():raise Refused()
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=target.parent,prefix=".jacred-golden-",suffix=".json")
    os.fchmod(fd,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
            stream.write("\n");stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run",action="store_true")
    p.add_argument("--base-url",required=True)
    p.add_argument("--cases",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--token-env",required=True,choices=["JACRED_LEGACY_API_KEY","JACRED_EDGE_API_KEY"])
    opts=p.parse_args()
    try:
        template=validate(opts.base_url,opts.cases)
        if not opts.run:
            print('{"status":"DRY_RUN_NO_NETWORK","cases":'+str(len(template["cases"]))+'}')
            return 0
        key=os.environ.get(opts.token_env,"")
        if len(key)<16:raise Refused()
        data=capture(opts.base_url,template["cases"],key)
        private_output(opts.output,data)
        print('{"status":"PRIVATE_CAPTURE_COMPLETE","contents":"DO_NOT_UPLOAD"}')
        return 0
    except (Refused,ValueError,OSError,UnicodeError):
        print('{"status":"CAPTURE_REFUSED"}',file=sys.stderr)
        return 2
if __name__=="__main__":sys.exit(main())
