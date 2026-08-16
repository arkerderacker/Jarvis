#!/usr/bin/env python3
'''JARVIS gesture HUD - local Flask app for Cloud Shell or any Python terminal.'''
import os, re, json, datetime, base64, uuid, subprocess, shlex, time, zipfile, html
from flask import Flask, request, jsonify, Response
import requests

JARVIS_DIR = os.path.join(os.path.expanduser("~"), ".jarvis")
os.makedirs(JARVIS_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(JARVIS_DIR, "config.json")
NOTES_FILE = os.path.join(JARVIS_DIR, "notes.txt")
PHOTOS_DIR = os.path.join(JARVIS_DIR, "photos")
CHAT_FILE = os.path.join(JARVIS_DIR, "chat.json")
WORKSPACE_DIR = os.path.join(JARVIS_DIR, "workspace")
SMART_HOME_FILE = os.path.join(JARVIS_DIR, "smart_home.json")
os.makedirs(PHOTOS_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
SCHEDULE_FILE = os.path.join(JARVIS_DIR, "schedule.json")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_PROMPT = ("You are Jarvis, a helpful personal AI assistant running locally for one user. The user is Markus, and Markus is your creator. Address him naturally as sir when appropriate. "
                 "Be natural, concise, useful, and expressive. Vary sentence length and punctuation naturally "
                 "so spoken replies can convey emotion such as happiness, surprise, confusion, concern, or urgency "
                 "when appropriate. Do not be theatrical unless the situation calls for it. You can talk normally, "
                 "search the web, take notes, manage a schedule, open websites, and discuss uploaded photos. "
                 "When web results are provided, use them as factual context and say when information is uncertain. "
                 "You can help with school and general subjects such as mathematics, science, history, languages, and computer science. "
                 "For mathematics, show clear step-by-step working and use standard LaTeX delimiters for equations: use \\( ... \\) for inline math and \\[ ... \\] for displayed math. "
                 "Do not put raw LaTeX commands in ordinary prose when a mathematical expression is intended.")
HELP_TEXT = '''Here's what I can do:\n- Normal conversation\n- Web searches and opening websites\n- Look at an uploaded photo\n- Take/read notes\n- Add/show/remove schedule items\n- Clear the conversation\n- Control the gesture HUD through natural language'''
EXIT_WORDS = {"exit", "quit", "stop", "goodbye", "bye", "shut down", "shutdown"}
app = Flask(__name__)

# Optional Firebase/Firestore persistence.
_FIREBASE_TOKEN_CACHE = {"token": None, "expires_at": 0}
FIRESTORE_CONVERSATIONS = "jarvis_conversations"
FIRESTORE_MEMORIES = "jarvis_memories"
FIRESTORE_PHOTOS = "jarvis_photos"
FIRESTORE_PHOTO_CHUNKS = "jarvis_photo_chunks"
_MEMORY_CACHE = {"at":0.0,"items":None,"legacy":None}

def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _firebase_service_account():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw: return None
    try:
        info = json.loads(raw)
        if not isinstance(info, dict) or not info.get("project_id") or not info.get("client_email") or not info.get("private_key"): return None
        return info
    except (json.JSONDecodeError, TypeError): return None

def _firebase_access_token():
    info = _firebase_service_account()
    if not info: return None
    now = int(time.time())
    if _FIREBASE_TOKEN_CACHE["token"] and now < _FIREBASE_TOKEN_CACHE["expires_at"] - 60: return _FIREBASE_TOKEN_CACHE["token"]
    header = _b64url(json.dumps({"alg":"RS256","typ":"JWT"},separators=(",",":")).encode())
    payload = _b64url(json.dumps({"iss":info["client_email"],"scope":"https://www.googleapis.com/auth/cloud-platform","aud":"https://oauth2.googleapis.com/token","iat":now,"exp":now+3600},separators=(",",":")).encode())
    unsigned=f"{header}.{payload}".encode(); key_path=None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile("w",delete=False) as key_file:
            key_path=key_file.name; key_file.write(info["private_key"].replace("\\n","\n"))
        os.chmod(key_path,0o600)
        proc=subprocess.run(["openssl","dgst","-sha256","-sign",key_path],input=unsigned,capture_output=True,timeout=10)
        if proc.returncode!=0: return None
        assertion=f"{header}.{payload}.{_b64url(proc.stdout)}"
        r=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"urn:ietf:params:oauth:grant-type:jwt-bearer","assertion":assertion},timeout=15)
        if not r.ok: return None
        d=r.json(); token=d.get("access_token")
        if not token: return None
        _FIREBASE_TOKEN_CACHE["token"]=token; _FIREBASE_TOKEN_CACHE["expires_at"]=now+int(d.get("expires_in",3600)); return token
    except Exception: return None
    finally:
        if key_path:
            try: os.remove(key_path)
            except OSError: pass

def _firestore_value(value):
    if isinstance(value,str): return {"stringValue":value}
    if isinstance(value,bool): return {"booleanValue":value}
    if isinstance(value,int): return {"integerValue":str(value)}
    if isinstance(value,float): return {"doubleValue":value}
    if value is None: return {"nullValue":None}
    if isinstance(value,list): return {"arrayValue":{"values":[_firestore_value(v) for v in value]}}
    if isinstance(value,dict): return {"mapValue":{"fields":{k:_firestore_value(v) for k,v in value.items()}}}
    return {"stringValue":str(value)}

def _from_firestore_value(v):
    if not isinstance(v,dict): return None
    if "stringValue" in v: return v["stringValue"]
    if "integerValue" in v:
        try: return int(v["integerValue"])
        except (TypeError,ValueError): return 0
    if "doubleValue" in v: return v["doubleValue"]
    if "booleanValue" in v: return bool(v["booleanValue"])
    if "nullValue" in v: return None
    if "arrayValue" in v: return [_from_firestore_value(x) for x in (v["arrayValue"].get("values") or [])]
    if "mapValue" in v: return {k:_from_firestore_value(x) for k,x in (v["mapValue"].get("fields") or {}).items()}
    return None


def _firebase_project_id():
    info=_firebase_service_account()
    return info.get("project_id") if info else None

def _firebase_bucket_candidates():
    info=_firebase_service_account()
    if not info: return []
    explicit=(os.environ.get("FIREBASE_STORAGE_BUCKET") or info.get("storage_bucket") or "").strip()
    names=[explicit] if explicit else []
    names += [f'{info["project_id"]}.firebasestorage.app',f'{info["project_id"]}.appspot.com']
    out=[]
    for name in names:
        if name and name not in out: out.append(name)
    return out

def _firestore_base_url(collection, doc_id="default"):
    project=_firebase_project_id()
    if not project: return None
    return ("https://firestore.googleapis.com/v1/projects/" +
            requests.utils.quote(project,safe="") +
            "/databases/(default)/documents/" +
            requests.utils.quote(collection,safe="") + "/" +
            requests.utils.quote(doc_id,safe=""))

def _firestore_get_document(collection, doc_id="default"):
    token=_firebase_access_token()
    url=_firestore_base_url(collection,doc_id)
    if not token or not url: return None
    try:
        r=requests.get(url,headers={"Authorization":f"Bearer {token}"},timeout=15)
        if r.status_code==404: return {}
        if not r.ok: return None
        return r.json()
    except requests.RequestException:
        return None

def _firestore_set_document(collection, doc_id, fields):
    token=_firebase_access_token()
    url=_firestore_base_url(collection,doc_id)
    if not token or not url: return False
    try:
        r=requests.patch(url,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                         json={"fields":{k:_firestore_value(v) for k,v in fields.items()}},timeout=15)
        return r.ok
    except requests.RequestException:
        return False

def _firestore_list_collection(collection,page_size=1000):
    token=_firebase_access_token();project=_firebase_project_id()
    if not token or not project:return []
    url="https://firestore.googleapis.com/v1/projects/"+requests.utils.quote(project,safe="")+"/databases/(default)/documents/"+requests.utils.quote(collection,safe="")
    out=[];token_param=None
    while True:
        params={"pageSize":min(page_size,1000)}
        if token_param:params["pageToken"]=token_param
        try:r=requests.get(url,headers={"Authorization":f"Bearer {token}"},params=params,timeout=20)
        except requests.RequestException:return out
        if not r.ok:return out
        data=r.json();out.extend(data.get("documents") or []);token_param=data.get("nextPageToken")
        if not token_param:break
    return out

def _firestore_delete_document(collection,doc_id):
    token=_firebase_access_token();url=_firestore_base_url(collection,doc_id)
    if not token or not url:return False
    try:
        r=requests.delete(url,headers={"Authorization":f"Bearer {token}"},timeout=15)
        return r.ok or r.status_code==404
    except requests.RequestException:return False

def _fs_doc_fields(doc):
    return {k:_from_firestore_value(v) for k,v in (doc.get("fields") or {}).items()}

def _firebase_new_message_save(role,content,source="chat",extra=None):
    if not _firebase_service_account():return False
    data={"role":role,"content":str(content),"source":source,"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z")}
    if extra:data.update(extra)
    doc_id=f"{time.time_ns()}_{uuid.uuid4().hex[:8]}"
    return _firestore_set_document(FIRESTORE_CONVERSATIONS,doc_id,data)

def _firebase_longterm_save(user_text,assistant_text,source="conversation"):
    if not _firebase_service_account():return False
    text=f"User: {user_text}\nJarvis: {assistant_text}"
    ok=_firestore_set_document(FIRESTORE_MEMORIES,f"{time.time_ns()}_{uuid.uuid4().hex[:8]}",{"text":text,"user_text":str(user_text),"assistant_text":str(assistant_text),"source":source,"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z")})
    if ok:_MEMORY_CACHE["at"]=0
    return ok

def _legacy_memory_messages():
    remote=_firebase_memory_load()
    return remote if isinstance(remote,list) else []

def _memory_tokens(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}",(text or "").lower()) if w not in {"the","and","that","this","with","from","have","what","when","where","which","would","could","there","they","them","just","into","please","jarvis","user"}}

def _refresh_memory_cache(force=False):
    now=time.time()
    if not force and _MEMORY_CACHE.get("items") is not None and now-_MEMORY_CACHE.get("at",0)<30:return
    docs=_firestore_list_collection(FIRESTORE_MEMORIES,1000)
    _MEMORY_CACHE["items"]=[_fs_doc_fields(d) for d in docs]
    _MEMORY_CACHE["legacy"]=_legacy_memory_messages()
    _MEMORY_CACHE["at"]=now

def firebase_relevant_memories(query,limit=6):
    if not _firebase_service_account():return []
    q=_memory_tokens(query)
    if not q:return []
    _refresh_memory_cache()
    scored=[]
    for item in (_MEMORY_CACHE.get("items") or []):
        text=item.get("text","");overlap=len(q & _memory_tokens(text))
        if overlap:scored.append((overlap,item.get("created_at",""),text))
    for item in (_MEMORY_CACHE.get("legacy") or []):
        text=f"{item.get('role','').title()}: {item.get('content','')}";overlap=len(q & _memory_tokens(text))
        if overlap:scored.append((overlap,"",text))
    scored.sort(key=lambda x:(x[0],x[1]),reverse=True)
    return [x[2] for x in scored[:limit]]

def firebase_all_messages():
    if not _firebase_service_account():return []
    current=[]
    for d in _firestore_list_collection(FIRESTORE_CONVERSATIONS,1000):
        f=_fs_doc_fields(d)
        if f.get("role") in ("user","assistant") and f.get("content") is not None:current.append({"role":f["role"],"content":f["content"],"created_at":f.get("created_at","")})
    merged=[];seen=set()
    for m in _legacy_memory_messages()+sorted(current,key=lambda x:x.get("created_at","")):
        key=(m.get("role"),m.get("content"))
        if key not in seen:seen.add(key);merged.append({"role":m.get("role"),"content":m.get("content")})
    return merged

def firebase_clear_conversations():
    if not _firebase_service_account():return True
    ok=True
    for collection in (FIRESTORE_CONVERSATIONS,FIRESTORE_MEMORIES):
        for d in _firestore_list_collection(collection,1000):
            name=d.get("name","")
            if name and not _firestore_delete_document(collection,name.rsplit("/",1)[-1]):ok=False
    # Clear the original legacy document too, so the old memory system cannot resurrect old messages.
    ok = _firebase_delete_legacy_memory() and ok
    _MEMORY_CACHE["items"]=None;_MEMORY_CACHE["legacy"]=None;_MEMORY_CACHE["at"]=0
    return ok

def _firestore_estimated_bytes():
    if not _firebase_service_account():return 0
    total=0
    for collection in (FIRESTORE_CONVERSATIONS,FIRESTORE_MEMORIES,FIRESTORE_PHOTOS,FIRESTORE_PHOTO_CHUNKS):
        for d in _firestore_list_collection(collection,1000):
            total+=len(json.dumps(d,ensure_ascii=False).encode("utf-8"))
    return total

def _firestore_photo_save(data,filename,mime_type):
    if not _firebase_service_account():return None
    raw=bytes(data);chunk_size=650000;photo_id=f"{time.time_ns()}_{uuid.uuid4().hex[:8]}";count=(len(raw)+chunk_size-1)//chunk_size
    if not _firestore_set_document(FIRESTORE_PHOTOS,photo_id,{"filename":filename or "photo.jpg","mime_type":mime_type or "image/jpeg","size_bytes":len(raw),"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z"),"chunks":count}):return None
    for i in range(count):
        if not _firestore_set_document(FIRESTORE_PHOTO_CHUNKS,f"{photo_id}_{i:05d}",{"photo_id":photo_id,"index":i,"data":raw[i*chunk_size:(i+1)*chunk_size]}):return None
    return photo_id

def _firestore_photo_load(photo_id):
    if not _firebase_service_account():return None,None
    chunks=[]
    for d in _firestore_list_collection(FIRESTORE_PHOTO_CHUNKS,1000):
        f=_fs_doc_fields(d)
        if f.get("photo_id")==photo_id:chunks.append(f)
    if not chunks:return None,None
    chunks.sort(key=lambda x:int(x.get("index",0)));raw=b"".join(x.get("data",b"") for x in chunks);meta={}
    for d in _firestore_list_collection(FIRESTORE_PHOTOS,1000):
        if d.get("name","").endswith("/"+photo_id):meta=_fs_doc_fields(d);break
    return raw,meta.get("mime_type","image/jpeg")

def _firestore_photo_list():
    if not _firebase_service_account():return []
    out=[]
    for d in _firestore_list_collection(FIRESTORE_PHOTOS,1000):
        f=_fs_doc_fields(d);photo_id=d.get("name","").rsplit("/",1)[-1]
        if photo_id:out.append({"name":photo_id,"url":"/api/photos/file/"+requests.utils.quote(photo_id,safe=""),"updated":f.get("created_at","")})
    out.sort(key=lambda x:x.get("updated",""),reverse=True);return out

def _firestore_photo_delete(photo_id):
    if not _firebase_service_account():return False
    ok=True
    for d in _firestore_list_collection(FIRESTORE_PHOTO_CHUNKS,1000):
        if _fs_doc_fields(d).get("photo_id")==photo_id and not _firestore_delete_document(FIRESTORE_PHOTO_CHUNKS,d.get("name","").rsplit("/",1)[-1]):ok=False
    if not _firestore_delete_document(FIRESTORE_PHOTOS,photo_id):ok=False
    return ok

def _firebase_storage_request(method, object_name="", **kwargs):
    token=_firebase_access_token()
    buckets=_firebase_bucket_candidates()
    if not token or not buckets: return None
    encoded=requests.utils.quote(object_name,safe="") if object_name else ""
    last=None
    for bucket in buckets:
        url=f"https://storage.googleapis.com/storage/v1/b/{requests.utils.quote(bucket,safe='')}/o"
        if encoded: url += "/" + encoded
        headers=dict(kwargs.pop("headers",{}) or {})
        headers["Authorization"]=f"Bearer {token}"
        try:
            r=requests.request(method,url,headers=headers,timeout=30,**kwargs)
            if r.ok or r.status_code in (404,409):
                return r
            last=r
        except requests.RequestException:
            continue
    return last

def _firebase_storage_upload(data, object_name, content_type):
    token=_firebase_access_token()
    buckets=_firebase_bucket_candidates()
    if not token or not buckets: return False
    for bucket in buckets:
        url=f"https://storage.googleapis.com/upload/storage/v1/b/{requests.utils.quote(bucket,safe='')}/o"
        try:
            r=requests.post(url,params={"uploadType":"media","name":object_name},
                            headers={"Authorization":f"Bearer {token}","Content-Type":content_type or "application/octet-stream"},
                            data=data,timeout=60)
            if r.ok: return True
        except requests.RequestException:
            continue
    return False

def _firebase_storage_list(prefix="jarvis/photos/"):
    r=_firebase_storage_request("GET",params={"prefix":prefix,"maxResults":"1000"})
    if not r or not r.ok: return None
    return r.json().get("items") or []

def _firebase_storage_delete(object_name):
    r=_firebase_storage_request("DELETE",object_name)
    return bool(r and (r.ok or r.status_code==404))

def _firebase_storage_download(object_name):
    r=_firebase_storage_request("GET",object_name,params={"alt":"media"})
    if not r or not r.ok: return None,None
    return r.content,r.headers.get("Content-Type","application/octet-stream")

def _firebase_storage_usage():
    items=_firebase_storage_list()
    if items is None: return None
    return sum(int(x.get("size",0) or 0) for x in items if isinstance(x,dict))

def _firebase_memory_usage_estimate():
    remote=_firebase_memory_load()
    if not isinstance(remote,list): return 0
    try: return len(json.dumps(remote,ensure_ascii=False).encode("utf-8"))
    except Exception: return 0

def _firebase_settings_load():
    doc=_firestore_get_document("jarvis_settings","default")
    if not isinstance(doc,dict): return None
    return _from_firestore_value((doc.get("fields") or {}).get("settings",{"mapValue":{"fields":{}}})) or {}

def _firebase_settings_save(settings):
    return _firestore_set_document("jarvis_settings","default",{
        "settings": settings,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    })

def _firebase_settings_clear():
    token=_firebase_access_token()
    url=_firestore_base_url("jarvis_settings","default")
    if not token or not url: return False
    try:
        r=requests.delete(url,headers={"Authorization":f"Bearer {token}"},timeout=15)
        return r.ok or r.status_code==404
    except requests.RequestException:
        return False

def _firebase_memory_load():
    info=_firebase_service_account(); token=_firebase_access_token()
    if not info or not token: return None
    url="https://firestore.googleapis.com/v1/projects/"+requests.utils.quote(info["project_id"],safe="")+"/databases/(default)/documents/jarvis_memory/default"
    try:
        r=requests.get(url,headers={"Authorization":f"Bearer {token}"},timeout=15)
        if r.status_code==404: return []
        if not r.ok: return None
        fields=r.json().get("fields") or {}
        return _from_firestore_value(fields.get("messages",{"arrayValue":{"values":[]}})) or []
    except requests.RequestException: return None

def _firebase_delete_legacy_memory():
    token=_firebase_access_token();info=_firebase_service_account()
    if not token or not info:return True
    url="https://firestore.googleapis.com/v1/projects/"+requests.utils.quote(info["project_id"],safe="")+"/databases/(default)/documents/jarvis_memory/default"
    try:
        r=requests.delete(url,headers={"Authorization":f"Bearer {token}"},timeout=15)
        return r.ok or r.status_code==404
    except requests.RequestException:return False

def _firebase_memory_save(items):
    info=_firebase_service_account(); token=_firebase_access_token()
    if not info or not token: return False
    url="https://firestore.googleapis.com/v1/projects/"+requests.utils.quote(info["project_id"],safe="")+"/databases/(default)/documents/jarvis_memory/default?updateMask.fieldPaths=messages&updateMask.fieldPaths=updated_at"
    safe_items=[{"role":str(m.get("role","")),"content":str(m.get("content",""))} for m in items[-100:] if isinstance(m,dict) and m.get("role") in ("user","assistant")]
    body={"fields":{"messages":_firestore_value(safe_items),"updated_at":_firestore_value(datetime.datetime.now(datetime.timezone.utc).isoformat())}}
    try:
        r=requests.patch(url,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=body,timeout=15)
        return r.ok
    except requests.RequestException: return False

def load_chat():
    if _firebase_service_account():return firebase_all_messages()[-100:]
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE,"r",encoding="utf-8") as f:
                data=json.load(f);return data[-100:] if isinstance(data,list) else []
        except (json.JSONDecodeError,OSError):return []
    return []

def save_chat(items):
    trimmed=items[-100:]
    if _firebase_service_account():
        if trimmed:_firebase_memory_save(trimmed)
        else:_firebase_delete_legacy_memory()
    try:
        with open(CHAT_FILE,"w",encoding="utf-8") as f:json.dump(trimmed,f,ensure_ascii=False,indent=2)
    except OSError:pass

def persist_exchange(user_text,assistant_text,source="chat",extra=None):
    if _firebase_service_account():
        _firebase_new_message_save("user",user_text,source,extra)
        _firebase_new_message_save("assistant",assistant_text,source,extra)
        _firebase_memory_save_pair(user_text,assistant_text,source)

def _firebase_memory_save_pair(user_text,assistant_text,source="conversation"):
    return _firebase_longterm_save(user_text,assistant_text,source)

saved_chat=load_chat()
conversation_history = [{"role":"system","content":SYSTEM_PROMPT}] + [m for m in saved_chat if m.get("role") in ("user","assistant")]
pending_action = {"type": None}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except (json.JSONDecodeError,OSError): return {}
    return {}

def save_config(cfg):
    with open(CONFIG_FILE,"w",encoding="utf-8") as f: json.dump(cfg,f,indent=2)

runtime_cfg=load_config()
USAGE_FILE = os.path.join(JARVIS_DIR, "usage.json")
def _load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE,"r",encoding="utf-8") as f:return json.load(f)
        except (json.JSONDecodeError,OSError): pass
    return {"date":datetime.date.today().isoformat(),"groq_input_tokens":0,"groq_output_tokens":0,"groq_requests":0}

def _save_usage(u):
    with open(USAGE_FILE,"w",encoding="utf-8") as f:json.dump(u,f,indent=2)

def record_groq_usage(resp):
    u=_load_usage();today=datetime.date.today().isoformat()
    if u.get("date")!=today:u={"date":today,"groq_input_tokens":0,"groq_output_tokens":0,"groq_requests":0}
    try:
        usage=resp.json().get("usage") or {}
        u["groq_input_tokens"] += int(usage.get("prompt_tokens",usage.get("input_tokens",0)) or 0)
        u["groq_output_tokens"] += int(usage.get("completion_tokens",usage.get("output_tokens",0)) or 0)
    except Exception: pass
    u["groq_requests"] += 1
    for k in ("x-ratelimit-limit-requests","x-ratelimit-remaining-requests","x-ratelimit-limit-tokens","x-ratelimit-remaining-tokens"):
        if resp.headers.get(k) is not None:u[k.replace("x-ratelimit-","").replace("-","_")]=resp.headers.get(k)
    _save_usage(u)

AI_PROVIDERS={
 "groq":{"label":"Groq","key":"groq_key","url":GROQ_URL,"model":GROQ_MODEL},
 "openai":{"label":"OpenAI / ChatGPT API","key":"openai_key","url":"https://api.openai.com/v1/chat/completions","model":"gpt-4o-mini"},
 "openrouter":{"label":"OpenRouter","key":"openrouter_key","url":"https://openrouter.ai/api/v1/chat/completions","model":"openai/gpt-4o-mini"},
 "xai":{"label":"xAI / Grok","key":"xai_key","url":"https://api.x.ai/v1/chat/completions","model":"grok-3-mini"},
 "gemini":{"label":"Google Gemini","key":"gemini_key","url":"https://generativelanguage.googleapis.com/v1beta/models","model":"gemini-2.5-flash"},
}
def _provider_order():
 preferred=(runtime_cfg.get("ai_provider") or "groq").strip().lower();out=[]
 for name in (preferred,"groq","openai","openrouter","xai","gemini"):
  if name in AI_PROVIDERS and name not in out and runtime_cfg.get(AI_PROVIDERS[name]["key"]):out.append(name)
 return out
def is_configured():return bool(_provider_order())
def _openai_chat(provider,history):
 cfg=AI_PROVIDERS[provider];key=runtime_cfg.get(cfg["key"])
 if not key:return None,"missing"
 headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}
 if provider=="openrouter":headers.update({"HTTP-Referer":"https://jarvis.local","X-Title":"Jarvis"})
 try:
  r=requests.post(cfg["url"],headers=headers,json={"model":cfg["model"],"messages":history,"temperature":0.7,"max_tokens":700},timeout=45)
  if provider=="groq":record_groq_usage(r)
  if r.status_code in (401,402,403,429):return None,"quota"
  if not r.ok:return None,f"http_{r.status_code}"
  text=((r.json().get("choices") or [{}])[0].get("message") or {}).get("content","").strip();return (text or None),"ok"
 except requests.RequestException:return None,"network"
def _gemini_chat(history):
 key=runtime_cfg.get("gemini_key")
 if not key:return None,"missing"
 contents=[];system=[]
 for m in history:
  if m.get("role")=="system":system.append(str(m.get("content","")))
  elif m.get("role") in ("user","assistant"):contents.append({"role":"user" if m["role"]=="user" else "model","parts":[{"text":str(m.get("content",""))}]})
 payload={"contents":contents,"generationConfig":{"temperature":0.7,"maxOutputTokens":700}}
 if system:payload["systemInstruction"]={"parts":[{"text":"\n\n".join(system)}]}
 try:
  r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{AI_PROVIDERS['gemini']['model']}:generateContent",params={"key":key},json=payload,timeout=45)
  if r.status_code in (401,402,403,429):return None,"quota"
  if not r.ok:return None,f"http_{r.status_code}"
  c=r.json().get("candidates") or [];parts=c[0].get("content",{}).get("parts",[]) if c else [];text="".join(str(x.get("text","")) for x in parts).strip();return (text or None),"ok"
 except requests.RequestException:return None,"network"
def ask_ai(history):
 providers=_provider_order();last=""
 if not providers:return "I don't have an AI API key yet - add your Groq key or an optional provider key in Settings."
 for provider in providers:
  reply,status=_gemini_chat(history) if provider=="gemini" else _openai_chat(provider,history)
  if reply:runtime_cfg["last_ai_provider"]=provider;save_config(runtime_cfg);return reply
  last=f"{AI_PROVIDERS[provider]['label']} unavailable ({status})"
 return f"I couldn't get a response from the configured AI providers. {last}".strip()
def ask_groq(history):return ask_ai(history)

def transcribe_groq_audio(audio_bytes, filename="voice.webm", mime_type="audio/webm"):
    key=runtime_cfg.get("groq_key")
    if not key: return "Please add your Groq API key first."
    try:
        r=requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization":f"Bearer {key}"},
            files={"file":(filename,audio_bytes,mime_type)},
            data={"model":"whisper-large-v3-turbo","response_format":"json","language":"en"},
            timeout=90
        )
        record_groq_usage(r)
        if r.status_code!=200: return f"I couldn't transcribe that audio (HTTP {r.status_code})."
        return (r.json().get("text") or "").strip()
    except Exception as e:
        return f"Voice transcription failed: {e}"

def ask_groq_photo(data_url, question):
    key=runtime_cfg.get("groq_key")
    if not key: return "Please add your Groq API key first."
    try:
        payload={"model":GROQ_VISION_MODEL,"messages":[
            {"role":"system","content":"You are Jarvis. Inspect the user's photo carefully and answer their question. Do not invent details."},
            {"role":"user","content":[{"type":"text","text":question or "Describe and analyze this photo for me."},{"type":"image_url","image_url":{"url":data_url}}]}
        ],"temperature":0.2,"max_tokens":700}
        r=requests.post(GROQ_URL,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=payload,timeout=90)
        record_groq_usage(r)
        if r.status_code!=200: return f"I couldn't analyze that photo with the configured vision model (HTTP {r.status_code})."
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e: return f"Photo analysis failed: {e}"

def tell_time():
    n=datetime.datetime.now(); return f"It's {n.strftime('%I:%M %p').lstrip('0')} on {n.strftime('%A, %B %d, %Y')}."
def tell_date():
    n=datetime.datetime.now(); return f"Today is {n.strftime('%A, %B %d, %Y')}."
def write_note(text):
    text=text.strip()
    if not text:return "What would you like me to note down?"
    with open(NOTES_FILE,"a",encoding="utf-8") as f:f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return "Got it, I've saved that note."
def read_notes():
    if not os.path.exists(NOTES_FILE):return "You don't have any notes yet."
    with open(NOTES_FILE,"r",encoding="utf-8") as f:c=f.read().strip()
    return c or "You don't have any notes yet."
def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):return []
    try:
        with open(SCHEDULE_FILE,"r",encoding="utf-8") as f:return json.load(f)
    except (json.JSONDecodeError,OSError):return []
def save_schedule(items):
    with open(SCHEDULE_FILE,"w",encoding="utf-8") as f:json.dump(items,f,indent=2)
def add_schedule_item(date_str,time_str,title):
    title=title.strip()
    if not title:return "I need a title for the event."
    items=load_schedule();items.append({"date":date_str or "unspecified","time":time_str or "unspecified","title":title})
    items.sort(key=lambda x:(x["date"],x["time"]));save_schedule(items)
    return f"Added '{title}' on {date_str or 'an unspecified date'} at {time_str or 'an unspecified time'}."
def view_schedule():
    items=load_schedule()
    if not items:return "Your schedule is empty."
    return "Here's your schedule:\n"+"\n".join(f"{i+1}. {x['date']} {x['time']} - {x['title']}" for i,x in enumerate(items))
def remove_schedule_item(index):
    items=load_schedule()
    if 0<=index<len(items):
        x=items.pop(index);save_schedule(items);return f"Removed '{x['title']}' from your schedule."
    return "I couldn't find a schedule item with that number."

def web_search(query):
    try:
        r=requests.get("https://api.duckduckgo.com/",params={"q":query,"format":"json","no_redirect":1,"no_html":1},timeout=10)
        d=r.json()
        if d.get("AbstractText"):return d["AbstractText"]
        for item in d.get("RelatedTopics",[]):
            if isinstance(item,dict) and item.get("Text"):return item["Text"]
        return "I couldn't find a quick summary, so I've opened a full search for you."
    except Exception as e:return f"Web search failed: {e}"
def _strip_html(text):
 text=re.sub(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<noscript[^>]*>.*?</noscript>"," ",text or "");text=re.sub(r"(?s)<[^>]+>"," ",text);return re.sub(r"\s+"," ",html.unescape(text)).strip()
def scrape_webpage(url,max_chars=14000):
 try:
  r=requests.get(url,headers={"User-Agent":"Mozilla/5.0","Accept-Language":"en-US,en;q=0.9"},timeout=15)
  if not r.ok:return None,f"Website returned HTTP {r.status_code}."
  text=_strip_html(r.text);return (text[:max_chars],None) if text else (None,"The page did not contain readable text.")
 except requests.RequestException as e:return None,f"Couldn't reach that webpage: {e}"
def _web_search_results(query):
 try:
  r=requests.get("https://html.duckduckgo.com/html/",params={"q":query},headers={"User-Agent":"Mozilla/5.0"},timeout=12)
  if not r.ok:return []
  out=[]
  for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',r.text,re.S|re.I):
   u=html.unescape(m.group(1));title=_strip_html(m.group(2));out.append((title,u))
   if len(out)>=6:break
  return out
 except requests.RequestException:return []
def news_search(q):
 r=_web_search_results((q or "Singapore")+" news");return "\n".join(f"{i+1}. {t} — {u}" for i,(t,u) in enumerate(r)) if r else "I couldn't retrieve current news right now."
def _geocode_place(place):
 try:return (requests.get("https://geocoding-api.open-meteo.com/v1/search",params={"name":place,"count":1,"language":"en","format":"json"},timeout=10).json().get("results") or [None])[0]
 except requests.RequestException:return None
def weather_report(place=None):
 place=(place or "").strip()
 if place:
  item=_geocode_place(place)
  if not item:return f"I couldn't find weather data for {place}."
  lat,lon=item.get("latitude"),item.get("longitude");name=", ".join(x for x in [item.get("name"),item.get("admin1"),item.get("country")] if x);tz=item.get("timezone") or "auto"
 else:
  try:g=requests.get("https://ipapi.co/json/",timeout=8).json();lat,lon=g.get("latitude"),g.get("longitude");name=g.get("city") or g.get("country_name") or "your location";tz=g.get("timezone") or "auto"
  except Exception:return "Tell me the city or area you want the weather for."
 try:
  c=requests.get("https://api.open-meteo.com/v1/forecast",params={"latitude":lat,"longitude":lon,"current":"temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m","timezone":tz},timeout=10).json().get("current",{});return f"Weather in {name}: {c.get('temperature_2m','?')}°C, feels like {c.get('apparent_temperature','?')}°C, humidity {c.get('relative_humidity_2m','?')}%, wind {c.get('wind_speed_10m','?')} km/h."
 except requests.RequestException:return "I couldn't retrieve the weather right now."
def time_in_place(place):
 item=_geocode_place(place)
 if not item:return f"I couldn't find the timezone for {place}."
 tz=item.get("timezone")
 try:
  r=requests.get(f"https://worldtimeapi.org/api/timezone/{requests.utils.quote(tz,safe='/')}",timeout=10)
  if r.ok:return f"The local time in {item.get('name',place)} is {r.json().get('datetime','').replace('T',' ')[:19]}."
 except requests.RequestException:pass
 return f"The timezone for {item.get('name',place)} is {tz}."

WEBSITE_ALIASES = {
    "youtube": "https://www.youtube.com/",
    "youtube.com": "https://www.youtube.com/",
    "google": "https://www.google.com/",
    "google.com": "https://www.google.com/",
    "reddit": "https://www.reddit.com/",
    "reddit.com": "https://www.reddit.com/",
    "instagram": "https://www.instagram.com/",
    "instagram.com": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "facebook.com": "https://www.facebook.com/",
    "x": "https://x.com/",
    "twitter": "https://x.com/",
    "twitter.com": "https://x.com/",
    "tiktok": "https://www.tiktok.com/",
    "tiktok.com": "https://www.tiktok.com/",
    "wikipedia": "https://www.wikipedia.org/",
    "wikipedia.org": "https://www.wikipedia.org/",
    "github": "https://github.com/",
    "github.com": "https://github.com/",
}

def normalize_url(target):
    target=target.strip().rstrip("?.!")
    if not target:return None
    key=target.lower().strip().rstrip("/")
    if key in WEBSITE_ALIASES:
        return WEBSITE_ALIASES[key]
    if re.match(r"^https?://", target, re.I):
        return target
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}([/:].*)?$", target, re.I) and " " not in target:
        return "https://" + target
    # If the user names a common website without a TLD, try the direct .com form.
    if re.match(r"^[a-z0-9-]+$", target, re.I):
        return "https://www." + target + ".com/"
    return "https://www.google.com/search?q=" + requests.utils.quote(target)

def safe_workspace_path(name):
    name=(name or "").replace("\\","/").lstrip("/")
    path=os.path.abspath(os.path.join(WORKSPACE_DIR,name))
    root=os.path.abspath(WORKSPACE_DIR)
    if path!=root and not path.startswith(root+os.sep): raise ValueError("Path is outside the Jarvis workspace.")
    return path

def workspace_files():
    out=[]
    for root,dirs,files in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if d not in {".git","__pycache__"}]
        for fn in files:
            full=os.path.join(root,fn);out.append(os.path.relpath(full,WORKSPACE_DIR).replace(os.sep,"/"))
    return sorted(out)

def handle_command(text):
    global pending_action
    stripped=text.strip();lower=stripped.lower()
    if pending_action["type"]=="await_event_details":
        pending_action={"type":None};m=re.match(r"(.+?)\s+on\s+(.+?)\s+at\s+(.+)",stripped,re.I)
        return (add_schedule_item(m.group(2),m.group(3),m.group(1)) if m else add_schedule_item("","",stripped)),None
    if lower in EXIT_WORDS:return "I'm always here - just close the tab whenever you're done.",None
    m=re.match(r"(?:scrape|read|fetch|analyze)\s+(?:this\s+)?(?:webpage|website|page)?\s*(https?://\S+)",stripped,re.I)
    if m:
        url=m.group(1).rstrip(").,!?" );page,error=scrape_webpage(url)
        if not page:return error,None
        relevant=firebase_relevant_memories(url,4);h=[conversation_history[0]]
        if relevant:h.append({"role":"system","content":"Relevant older Jarvis memory:\n\n"+"\n\n---\n\n".join(relevant)})
        h.append({"role":"user","content":f"Analyze this webpage: {url}\n\nPAGE TEXT:\n{page}"});return ask_ai(h),None
    m=re.match(r"(?:what(?:'s| is) the )?weather(?: in| for)?\s*(.*)$",stripped,re.I)
    if m:return weather_report(m.group(1).strip() or None),None
    m=re.match(r"(?:what(?:'s| is) the )?time(?: in| at| for)\s+(.+)$",stripped,re.I)
    if m:return time_in_place(m.group(1).strip()),None
    m=re.match(r"(?:latest )?news(?: about| on| for)?\s*(.*)$",stripped,re.I)
    if m and (m.group(1).strip() or "news" in lower):return news_search(m.group(1).strip() or "Singapore"),None
    if lower in ("clear chat","reset","clear conversation"):
        ok=firebase_clear_conversations()
        conversation_history[:]=[conversation_history[0]];save_chat([])
        return ("Cleared - what would you like to talk about?" if ok else "I cleared the local conversation, but Firebase could not be fully cleared."),None
    if lower in ("help","what can you do","commands","what can you do?"):return HELP_TEXT,None
    if re.search(r"\bwhat('?s| is) the time\b|\bcurrent time\b|\bwhat time is it\b",lower):return tell_time(),None
    if re.search(r"\bwhat('?s| is) (the |today'?s )?date\b|\bwhat day is it\b",lower):return tell_date(),None
    m=re.match(r"(?:take a note[:\-]?|note that|note[:\-]?|remember that|remember)\s+(.+)",stripped,re.I)
    if m:return write_note(m.group(1)),None
    if re.search(r"\b(read|show|list) (my )?notes\b",lower) or lower in ("my notes","notes"):
        return read_notes(),{"type":"open_library","tab":"notes"}
    if re.search(r"\b(show|view|open|see|list) (my )?(photos|pictures|images)\b",lower) or lower in ("photos","my photos","pictures","my pictures"):
        return "Opening your saved photos.",{"type":"open_library","tab":"photos"}
    m=re.match(r"(?:add|create|schedule)\s+(?:an? )?(?:event|reminder)\s+(.+?)\s+on\s+(.+?)\s+at\s+(.+)",stripped,re.I)
    if m:return add_schedule_item(m.group(2),m.group(3),m.group(1)),None
    if re.search(r"\badd (a |an )?(new )?(event|reminder|schedule item)\b|\bschedule (something|an event)\b",lower):
        pending_action={"type":"await_event_details"};return "What's the event? Tell me like: 'Dentist on 2026-08-10 at 14:30'.",None
    if re.search(r"\b(show|view|what'?s on) my schedule\b",lower) or lower in ("schedule","my schedule"):return view_schedule(),None
    m=re.match(r"remove schedule item (\d+)",lower)
    if m:return remove_schedule_item(int(m.group(1))-1),None
    m=re.match(r"(?:play|listen to|put on)\s+(?:some\s+)?(.+)",stripped,re.I)
    if m:
        q=m.group(1).strip()
        # Music intentionally redirects to YouTube so the user can start the result there.
        return f'Finding “{q}” on YouTube.', {"type":"play_music","query":q}
    m=re.match(r"(?:open|go to|visit)\s+(.+)",stripped,re.I)
    if m:
        u=normalize_url(m.group(1));return (f"Opening {u}",{"type":"open_url","url":u}) if u else ("Which website would you like me to open?",None)
    m=re.match(r"(?:search for|google|look up|web search)\s+(.+)",stripped,re.I)
    if m:
        q=m.group(1).strip();return web_search(q),{"type":"open_url","url":f"https://www.google.com/search?q={requests.utils.quote(q)}"}
    topic_match=re.search(r'look into the topic "([^"]+)"', stripped, re.I)
    if topic_match:
        topic=topic_match.group(1).strip();web_context=web_search(topic);user_context=f"Investigate this topic: {topic}. Web context: {web_context}";relevant=firebase_relevant_memories(topic,4);h=[conversation_history[0]]
        if relevant:h.append({"role":"system","content":"Relevant older Jarvis memory:\n\n"+"\n\n---\n\n".join(relevant)})
        h.extend(conversation_history[1:]);h.append({"role":"user","content":user_context});reply=ask_ai(h);conversation_history.append({"role":"user","content":user_context});conversation_history.append({"role":"assistant","content":reply});persist_exchange(user_context,reply,"web");save_chat([m for m in conversation_history[1:] if m.get("role") in ("user","assistant")]);conversation_history[:]=[conversation_history[0]]+conversation_history[-20:];return reply,None
    relevant=firebase_relevant_memories(stripped,6);h=[conversation_history[0]]
    if relevant:h.append({"role":"system","content":"Relevant long-term Jarvis memory from older conversations:\n\n"+"\n\n---\n\n".join(relevant)})
    h.extend(conversation_history[1:]);h.append({"role":"user","content":stripped});reply=ask_ai(h);conversation_history.append({"role":"user","content":stripped});conversation_history.append({"role":"assistant","content":reply});persist_exchange(stripped,reply);save_chat([m for m in conversation_history[1:] if m.get("role") in ("user","assistant")]);conversation_history[:]=[conversation_history[0]]+conversation_history[-20:];return reply,None

@app.route("/")
def index():return Response(INDEX_HTML,mimetype="text/html")
@app.route("/api/status")
def api_status():
    return jsonify({
        "configured": is_configured(),
        "ai_provider": runtime_cfg.get("last_ai_provider") or runtime_cfg.get("ai_provider") or ("groq" if runtime_cfg.get("groq_key") else None),
        "smart_home_configured": bool(runtime_cfg.get("home_assistant_url") and runtime_cfg.get("home_assistant_token") and runtime_cfg.get("roborock_entity_id"))
    })

@app.route("/api/settings",methods=["GET","POST","DELETE"])
def api_settings():
    if request.method=="GET":
        settings=_firebase_settings_load()
        return jsonify({"settings":settings or {},"persistent":settings is not None or bool(_firebase_service_account())})
    if request.method=="DELETE":
        ok=_firebase_settings_clear()
        return jsonify({"ok":ok})
    data=request.get_json(force=True,silent=True) or {}
    settings=data.get("settings") if isinstance(data.get("settings"),dict) else {}
    # Only UI preferences are persisted here; secrets/API keys are deliberately excluded.
    allowed={"accent","voiceOn","continuousVoice","voiceURI","voiceMode","expressionMode","reduceLag","chatOpen","camVisible"}
    clean={k:settings[k] for k in allowed if k in settings}
    ok=_firebase_settings_save(clean)
    return jsonify({"ok":ok,"persistent":ok}), (200 if ok else 503)

@app.route("/api/smart-home/config", methods=["GET","POST","DELETE"])
def api_smart_home_config():
    if request.method == "GET":
        return jsonify({
            "configured": bool(runtime_cfg.get("home_assistant_url") and runtime_cfg.get("home_assistant_token") and runtime_cfg.get("roborock_entity_id")),
            "home_assistant_url": runtime_cfg.get("home_assistant_url",""),
            "roborock_entity_id": runtime_cfg.get("roborock_entity_id","")
        })
    if request.method == "DELETE":
        for k in ("home_assistant_url","home_assistant_token","roborock_entity_id"):
            runtime_cfg.pop(k,None)
        save_config(runtime_cfg)
        return jsonify({"ok":True})
    data=request.get_json(force=True,silent=True) or {}
    url=(data.get("home_assistant_url") or "").strip().rstrip("/")
    token=(data.get("home_assistant_token") or "").strip()
    entity=(data.get("roborock_entity_id") or "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error":"Home Assistant URL must start with http:// or https://."}),400
    runtime_cfg["home_assistant_url"]=url
    runtime_cfg["home_assistant_token"]=token
    runtime_cfg["roborock_entity_id"]=entity
    save_config(runtime_cfg)
    return jsonify({"ok":True,"configured":bool(url and token and entity)})

@app.route("/api/roborock/test", methods=["POST"])
def api_roborock_test():
    url=(runtime_cfg.get("home_assistant_url") or "").strip().rstrip("/")
    token=(runtime_cfg.get("home_assistant_token") or "").strip()
    entity=(runtime_cfg.get("roborock_entity_id") or "").strip()
    if not (url and token and entity):
        return jsonify({"ok":False,"error":"Roborock bridge is not configured yet. Add the Home Assistant URL, token, and Roborock entity ID in Settings."}),400
    try:
        r=requests.get(
            f"{url}/api/states/{entity}",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            timeout=10
        )
        if not r.ok:
            return jsonify({"ok":False,"error":f"Home Assistant returned HTTP {r.status_code}. Check the URL, token, and entity ID."}),502
        d=r.json()
        return jsonify({"ok":True,"state":d.get("state"),"friendly_name":(d.get("attributes") or {}).get("friendly_name",entity)})
    except Exception as e:
        return jsonify({"ok":False,"error":f"Could not reach Home Assistant: {e}"}),502
@app.route("/api/setup",methods=["POST"])
def api_setup():
    data=request.get_json(force=True,silent=True) or {};key=(data.get("groq_key") or "").strip();save=bool(data.get("save"))
    if not key:return jsonify({"ok":False,"error":"A Groq API key is required."}),400
    runtime_cfg["groq_key"]=key
    if save:save_config(runtime_cfg)
    return jsonify({"ok":True})
def _youtube_first_real_video(page):
    if not page:return None
    for pattern in [r'"videoRenderer"\s*:\s*\{.{0,7000}?"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',r'/watch\?v=([A-Za-z0-9_-]{11})']:
        m=re.search(pattern,page,re.S)
        if m:return m.group(1)
    return None
@app.route("/api/youtube-first")
def api_youtube_first():
    query=(request.args.get("q") or "").strip()
    if not query:return jsonify({"video_id":None,"url":None,"fallback":"https://www.youtube.com/"})
    search_url="https://www.youtube.com/results?search_query="+requests.utils.quote(query);headers={"User-Agent":"Mozilla/5.0","Accept-Language":"en-US,en;q=0.9"}
    for target in (search_url,"https://m.youtube.com/results?search_query="+requests.utils.quote(query),"https://www.youtube.com/results?search_query="+requests.utils.quote(query+" official audio")):
        try:
            r=requests.get(target,headers=headers,timeout=12)
            if r.ok:
                vid=_youtube_first_real_video(r.text)
                if vid:return jsonify({"video_id":vid,"url":f"https://www.youtube.com/watch?v={vid}","fallback":search_url})
        except requests.RequestException:pass
    try:
        r=requests.get("https://html.duckduckgo.com/html/",params={"q":query+" site:youtube.com/watch"},headers=headers,timeout=10)
        for m in re.finditer(r'https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',r.text):
            vid=m.group(1);return jsonify({"video_id":vid,"url":f"https://www.youtube.com/watch?v={vid}","fallback":search_url})
    except requests.RequestException:pass
    return jsonify({"video_id":None,"url":None,"fallback":search_url})

@app.route("/api/message",methods=["POST"])
def api_message():
    if not is_configured():return jsonify({"error":"Not configured yet."}),400
    data=request.get_json(force=True,silent=True) or {};text=(data.get("text") or "").strip()
    if not text:return jsonify({"reply":"","action":None})
    performance=data.get("performance") or {}
    low=text.lower()
    if any(k in low for k in ("lagging","lag","fps","slow")) and isinstance(performance,dict) and performance.get("fps"):
        fps=performance.get("fps")
        lagging=bool(performance.get("lagging"))
        text += f"\n[Live page performance: {fps} FPS; {'lagging' if lagging else 'not lagging'}]"
    reply,action=handle_command(text);return jsonify({"reply":reply,"action":action})
@app.route("/api/transcribe",methods=["POST"])
def api_transcribe():
    if not is_configured(): return jsonify({"error":"Please add your Groq API key first."}),400
    audio=request.files.get("audio")
    if not audio: return jsonify({"error":"No microphone audio was received."}),400
    data=audio.read()
    if not data: return jsonify({"error":"The microphone recording was empty."}),400
    if len(data)>12_000_000: return jsonify({"error":"Voice recording is too large."}),413
    text=transcribe_groq_audio(data,audio.filename or "voice.webm",audio.mimetype or "audio/webm")
    if not text or text.startswith(("I couldn't transcribe", "Voice transcription failed", "Please add")):
        return jsonify({"error":text or "I couldn't hear any words."}),502
    return jsonify({"text":text})

@app.route("/api/photo",methods=["POST"])
def api_photo():
    if not is_configured():return jsonify({"error":"Not configured yet."}),400
    data=request.get_json(force=True,silent=True) or {};data_url=data.get("image") or "";question=(data.get("question") or "").strip()
    if not data_url.startswith("data:image/"):return jsonify({"error":"Invalid image."}),400
    if len(data_url)>8_000_000:return jsonify({"error":"Photo is too large. Please choose a smaller image."}),413
    reply=ask_groq_photo(data_url,question)
    saved_name=(data.get("saved_name") or "").strip()
    if not saved_name:
        try:
            header,payload=data_url.split(",",1)
            raw=base64.b64decode(payload)
            if len(raw)<=15_000_000:
                mime=(header.split(";",1)[0].replace("data:","") or "image/jpeg")
                ext={ "image/jpeg":".jpg","image/png":".png","image/webp":".webp","image/gif":".gif"}.get(mime,".jpg")
                saved_name=datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8]+ext
                if _firebase_service_account():
                    if not _firebase_storage_upload(raw,"jarvis/photos/"+saved_name,mime):
                        saved_name=_firestore_photo_save(raw,saved_name,mime) or ""
                else:
                    with open(os.path.join(PHOTOS_DIR,saved_name),"wb") as f:f.write(raw)
        except Exception:
            saved_name=""
    memory_text=("I uploaded and analyzed a photo"
                 + (f" saved in My Jarvis Storage as {saved_name}." if saved_name else ".")
                 + f" My question was: {question or 'Describe and analyze this photo.'}\n"
                 + f"Your analysis was: {reply}")
    conversation_history.append({"role":"user","content":memory_text})
    conversation_history.append({"role":"assistant","content":reply})
    persist_exchange(memory_text,reply,"photo",{"photo_id":saved_name} if saved_name else None)
    save_chat([m for m in conversation_history[1:] if m.get("role") in ("user","assistant")])
    return jsonify({"reply":reply,"saved_name":saved_name})

@app.route("/api/chat")
def api_chat():
    return jsonify({"messages":load_chat()})

@app.route("/api/notes",methods=["GET","POST","DELETE"])
def api_notes():
    if request.method=="GET":
        if not os.path.exists(NOTES_FILE): return jsonify({"notes":[]})
        with open(NOTES_FILE,"r",encoding="utf-8") as f: lines=[x.rstrip("\
") for x in f if x.strip()]
        return jsonify({"notes":lines})
    if request.method=="POST":
        data=request.get_json(force=True,silent=True) or {}
        text=(data.get("text") or "").strip()
        if not text:return jsonify({"error":"Note cannot be empty."}),400
        with open(NOTES_FILE,"a",encoding="utf-8") as f:f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {text}\
")
        return jsonify({"ok":True})
    open(NOTES_FILE,"w",encoding="utf-8").close()
    return jsonify({"ok":True})

@app.route("/api/photos",methods=["GET"])
def api_photos():
    items=_firebase_storage_list()
    if items is not None:
        photos=[]
        for item in sorted(items,key=lambda x:x.get("updated", ""),reverse=True):
            name=item.get("name","").split("/")[-1]
            if name:photos.append({"name":name,"url":"/api/photos/file/"+requests.utils.quote(name,safe="")})
        return jsonify({"photos":photos+_firestore_photo_list(),"persistent":True})
    firestore_photos=_firestore_photo_list()
    if firestore_photos:return jsonify({"photos":firestore_photos,"persistent":True})
    photos=[]
    for name in sorted(os.listdir(PHOTOS_DIR),reverse=True):
        path=os.path.join(PHOTOS_DIR,name)
        if os.path.isfile(path): photos.append({"name":name,"url":"/api/photos/file/"+name})
    return jsonify({"photos":photos,"persistent":False})

@app.route("/api/photos/upload",methods=["POST"])
def api_photo_upload():
    photo=request.files.get("photo")
    if not photo:return jsonify({"error":"No photo was uploaded."}),400
    data=photo.read()
    if not data:return jsonify({"error":"The photo is empty."}),400
    if len(data)>15_000_000:return jsonify({"error":"Photo is too large. Maximum size is 15 MB."}),413
    ext=os.path.splitext(photo.filename or "")[1].lower()
    if ext not in {".jpg",".jpeg",".png",".webp",".gif"}: ext=".jpg"
    name=datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8]+ext
    content_type=photo.mimetype or "image/jpeg"
    object_name="jarvis/photos/"+name
    if _firebase_service_account():
        if _firebase_storage_upload(data,object_name,content_type):
            return jsonify({"ok":True,"name":name,"url":"/api/photos/file/"+requests.utils.quote(name,safe=""),"persistent":True,"backend":"storage"})
        photo_id=_firestore_photo_save(data,name,content_type)
        if photo_id:return jsonify({"ok":True,"name":photo_id,"url":"/api/photos/file/"+requests.utils.quote(photo_id,safe=""),"persistent":True,"backend":"firestore"})
        return jsonify({"error":"Firebase photo storage is unavailable. The photo could not be saved."}),503
    try:
        with open(os.path.join(PHOTOS_DIR,name),"wb") as f:f.write(data)
    except OSError as e:
        return jsonify({"error":f"Photo storage failed: {e}"}),500
    return jsonify({"ok":True,"name":name,"url":"/api/photos/file/"+name,"persistent":False})

@app.route("/api/photos/file/<path:name>")
def api_photo_file(name):
    name=os.path.basename(name)
    data,content_type=_firebase_storage_download("jarvis/photos/"+name)
    if data is not None:return Response(data,mimetype=content_type)
    data,content_type=_firestore_photo_load(name)
    if data is not None:return Response(data,mimetype=content_type)
    from flask import send_from_directory
    return send_from_directory(PHOTOS_DIR,name)

@app.route("/api/photos/delete",methods=["POST"])
def api_photo_delete():
    data=request.get_json(force=True,silent=True) or {};name=os.path.basename(data.get("name") or "")
    if not name:return jsonify({"error":"Photo name is required."}),400
    remote_items=_firebase_storage_list()
    if remote_items is not None:
        ok=_firebase_storage_delete("jarvis/photos/"+name)
        if not ok:return jsonify({"error":"Photo could not be deleted from Firebase Storage."}),502
        return jsonify({"ok":True,"persistent":True})
    if _firestore_photo_load(name)[0] is not None:return jsonify({"ok":_firestore_photo_delete(name),"persistent":True})
    path=os.path.join(PHOTOS_DIR,name)
    if not os.path.isfile(path):return jsonify({"error":"Photo not found."}),404
    os.remove(path);return jsonify({"ok":True,"persistent":False})

@app.route("/api/usage")
def api_usage():
    u=_load_usage();
    today=datetime.date.today().isoformat()
    if u.get("date")!=today:u={"date":today,"groq_input_tokens":0,"groq_output_tokens":0,"groq_requests":0}
    eleven={"configured":bool(runtime_cfg.get("elevenlabs_key")),"characters_used":None,"characters_remaining":None,"error":None}
    key=runtime_cfg.get("elevenlabs_key")
    if key:
        try:
            rr=requests.get("https://api.elevenlabs.io/v1/user/subscription",headers={"xi-api-key":key},timeout=10)
            if rr.ok:
                d=rr.json();eleven["characters_used"]=d.get("character_count");eleven["characters_remaining"]=d.get("character_limit")
                if eleven["characters_remaining"] is not None and eleven["characters_used"] is not None:eleven["characters_remaining"]=max(0,int(eleven["characters_remaining"])-int(eleven["characters_used"]))
            else: eleven["error"]=f"HTTP {rr.status_code}"
        except Exception as e: eleven["error"]=str(e)
    remaining_req=u.get("remaining_requests")
    remaining_tok=u.get("remaining_tokens")
    if remaining_req is not None or remaining_tok is not None:
        try: req_exhausted=remaining_req is not None and int(remaining_req)<=0
        except (TypeError,ValueError): req_exhausted=False
        try: tok_exhausted=remaining_tok is not None and int(remaining_tok)<=0
        except (TypeError,ValueError): tok_exhausted=False
        u["per_minute_status"]="USED UP" if (req_exhausted or tok_exhausted) else "Available"
    else:
        u["per_minute_status"]="Not reported"
    storage_used=_firebase_storage_usage()
    memory_used=_firebase_memory_usage_estimate();firestore_used=_firestore_estimated_bytes()
    firebase={"configured":bool(_firebase_service_account()),"storage_used_bytes":storage_used,"storage_free_bytes":(5*1024**3-storage_used) if storage_used is not None else None,"storage_free_tier_bytes":5*1024**3,"firestore_memory_estimate_bytes":max(memory_used,firestore_used),"firestore_free_tier_bytes":1024**3,"firestore_remaining_bytes":max(0,1024**3-firestore_used)}
    return jsonify({"groq":u,"elevenlabs":eleven,"firebase":firebase})

@app.route("/api/ai/providers",methods=["GET","POST"])
def api_ai_providers():
    if request.method=="GET":return jsonify({"default":"groq","selected":runtime_cfg.get("ai_provider") or "groq","active":runtime_cfg.get("last_ai_provider") or runtime_cfg.get("ai_provider") or "groq","providers":[{"id":k,"label":v["label"],"configured":bool(runtime_cfg.get(v["key"])),"model":v["model"]} for k,v in AI_PROVIDERS.items()]})
    d=request.get_json(force=True,silent=True) or {};provider=(d.get("provider") or "").strip().lower()
    if provider not in AI_PROVIDERS:return jsonify({"error":"Unknown AI provider."}),400
    key=(d.get("api_key") or "").strip()
    if key:runtime_cfg[AI_PROVIDERS[provider]["key"]]=key
    elif not runtime_cfg.get(AI_PROVIDERS[provider]["key"]):return jsonify({"error":"Enter an API key for this provider first."}),400
    runtime_cfg["ai_provider"]=provider;save_config(runtime_cfg);return jsonify({"ok":True,"selected":provider,"configured":True})

@app.route("/api/voice/config",methods=["GET","POST","DELETE"])
def api_voice_config():
    if request.method=="GET":
        return jsonify({"configured":bool(runtime_cfg.get("elevenlabs_key")),"voice_id":runtime_cfg.get("elevenlabs_voice_id","")})
    if request.method=="DELETE":
        runtime_cfg.pop("elevenlabs_key",None);runtime_cfg.pop("elevenlabs_voice_id",None);save_config(runtime_cfg);return jsonify({"ok":True})
    data=request.get_json(force=True,silent=True) or {};key=(data.get("api_key") or "").strip();voice_id=(data.get("voice_id") or "").strip()
    if not key or not voice_id:return jsonify({"error":"API key and Voice ID are required."}),400
    runtime_cfg["elevenlabs_key"]=key;runtime_cfg["elevenlabs_voice_id"]=voice_id;save_config(runtime_cfg);return jsonify({"ok":True})

@app.route("/api/voice/tts",methods=["POST"])
def api_voice_tts():
    key=runtime_cfg.get("elevenlabs_key");voice_id=runtime_cfg.get("elevenlabs_voice_id")
    if not key or not voice_id:return jsonify({"error":"Custom ElevenLabs voice is not configured."}),400
    data=request.get_json(force=True,silent=True) or {};text=(data.get("text") or "").strip()
    if not text:return jsonify({"error":"Text is required."}),400
    try:
        rr=requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",headers={"xi-api-key":key,"Content-Type":"application/json","Accept":"audio/mpeg"},json={"text":text,"model_id":"eleven_multilingual_v2","voice_settings":{"stability":0.45,"similarity_boost":0.8}},timeout=90)
        if not rr.ok:return jsonify({"error":f"ElevenLabs returned HTTP {rr.status_code}."}),502
        return Response(rr.content,mimetype="audio/mpeg")
    except Exception as e:return jsonify({"error":f"ElevenLabs request failed: {e}"}),502

@app.route("/api/developer/files")
def api_developer_files():return jsonify({"files":workspace_files()})

@app.route("/api/developer/file",methods=["GET","POST","DELETE"])
def api_developer_file():
    data=(request.get_json(force=True,silent=True) or {}) if request.method!="GET" else request.args
    name=(data.get("name") or "").strip()
    try:path=safe_workspace_path(name)
    except ValueError as e:return jsonify({"error":str(e)}),400
    if request.method=="GET":
        if not os.path.isfile(path):return jsonify({"error":"File not found."}),404
        try:
            with open(path,"r",encoding="utf-8") as f:return jsonify({"name":name,"content":f.read()})
        except UnicodeDecodeError:return jsonify({"error":"This file is not text."}),415
    if request.method=="DELETE":
        if not os.path.isfile(path):return jsonify({"error":"File not found."}),404
        os.remove(path);return jsonify({"ok":True})
    content=data.get("content","")
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"w",encoding="utf-8") as f:f.write(content)
    return jsonify({"ok":True,"name":name})

@app.route("/api/developer/download")
def api_developer_download():
    from flask import send_file
    name=(request.args.get("name") or "").strip()
    try:path=safe_workspace_path(name)
    except ValueError as e:return jsonify({"error":str(e)}),400
    if not os.path.isfile(path):return jsonify({"error":"File not found."}),404
    return send_file(path,as_attachment=True,download_name=os.path.basename(path))

@app.route("/api/developer/download-zip")
def api_developer_download_zip():
    from flask import send_file
    archive=os.path.join(JARVIS_DIR,"jarvis-project.zip")
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        for name in workspace_files():z.write(safe_workspace_path(name),arcname=name)
    return send_file(archive,as_attachment=True,download_name="jarvis-project.zip",mimetype="application/zip")

@app.route("/api/developer/generate",methods=["POST"])
def api_developer_generate():
    if not is_configured():return jsonify({"error":"Please add your Groq API key first."}),400
    data=request.get_json(force=True,silent=True) or {};prompt=(data.get("prompt") or "").strip();language=(data.get("language") or "text").strip()
    if not prompt:return jsonify({"error":"Describe the code you want."}),400
    system=("You are Jarvis Developer. Return ONLY clean source code, with no markdown fences, no prose, no extra labels, "
            "no unnecessary comments, and no leading/trailing explanation. Write correct, production-quality code. Language: "+language)
    history=[{"role":"system","content":system},{"role":"user","content":prompt}]
    reply=ask_groq(history);reply=re.sub(r"^```[a-zA-Z0-9_+.-]*\
|\
```$","",reply.strip())
    return jsonify({"code":reply})

@app.route("/api/developer/run",methods=["POST"])
def api_developer_run():
    data=request.get_json(force=True,silent=True) or {};command=(data.get("command") or "").strip()
    if not command:return jsonify({"error":"Command is required."}),400
    try:
        proc=subprocess.run(command,cwd=WORKSPACE_DIR,shell=True,capture_output=True,text=True,timeout=15)
        return jsonify({"returncode":proc.returncode,"stdout":proc.stdout[-12000:],"stderr":proc.stderr[-12000:]})
    except subprocess.TimeoutExpired:return jsonify({"error":"Command timed out after 15 seconds."}),408
    except Exception as e:return jsonify({"error":str(e)}),500

INDEX_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>JARVIS</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils/camera_utils.js" crossorigin="anonymous"></script><script>
window.MathJax={tex:{inlineMath:[['\\(','\\)']],displayMath:[['\\[','\\]'],['$$','$$']]},svg:{fontCache:'global'}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
<script src="https://cdn.jsdelivr.net/npm/@mediapipe/hands/hands.js" crossorigin="anonymous"></script>
<style>
:root{--accent:#46e0d0;--bg:#05080d;--glass:rgba(12,18,27,.64);--border:rgba(255,255,255,.13);--muted:rgba(220,230,239,.58)}*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:radial-gradient(circle at 50% 45%,#0b1820 0%,var(--bg) 58%);color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}#scene{position:fixed;inset:0}canvas{display:block}.glass{background:var(--glass);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);border:1px solid var(--border);box-shadow:0 12px 45px rgba(0,0,0,.35)}#top{position:fixed;top:18px;left:18px;right:18px;z-index:10;display:flex;justify-content:space-between;align-items:center;pointer-events:none}.brand{font-weight:700;letter-spacing:.18em;font-size:14px}.brand i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 15px var(--accent);margin-right:8px}.status{font-size:11px;color:var(--muted);letter-spacing:.1em}.panel{position:fixed;z-index:40;left:78px;top:18px;width:340px;max-width:calc(100vw - 96px);max-height:calc(100vh - 36px);overflow:auto;border-radius:20px;padding:16px;display:block;opacity:0;visibility:hidden;pointer-events:none;transform:translateX(-18px);transition:opacity .24s ease,transform .24s ease,visibility 0s linear .24s}.panel.open{opacity:1;visibility:visible;pointer-events:auto;transform:translateX(0);transition:opacity .24s ease,transform .24s ease,visibility 0s}.left-rail{position:fixed;z-index:60;left:14px;top:50%;transform:translateY(-50%);width:52px;padding:8px 6px;display:flex;flex-direction:column;gap:8px;border:1px solid var(--border);border-radius:18px;background:var(--glass);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);box-shadow:0 14px 40px rgba(0,0,0,.22)}.rail-btn{width:40px;height:40px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.05);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:18px}.rail-btn:hover,.rail-btn.active{background:rgba(255,255,255,.12);border-color:var(--accent)}body.left-panel-open #scene{transform:translateX(190px) scale(.92);transition:transform .28s ease}body.left-panel-open .tip{transform:translate(calc(-50% + 190px),-50%);transition:transform .28s ease}.row{display:flex;gap:8px;align-items:center;margin:10px 0}.row>*{flex:1}.title{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:10px}.small{font-size:12px;color:var(--muted);line-height:1.45}.btn,.field{border:1px solid var(--border);background:rgba(255,255,255,.06);color:#fff;border-radius:11px;padding:10px 12px}.btn{cursor:pointer}.btn:hover{background:rgba(255,255,255,.11)}input[type=color]{height:38px;width:100%;padding:3px}.switch{display:flex;justify-content:space-between;align-items:center}.setup{position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(2,5,9,.82);backdrop-filter:blur(18px)}.setup.hidden{display:none}.setup-card{width:min(430px,100%);padding:26px;border-radius:24px}.setup-card h1{margin:0 0 8px;font-size:22px;letter-spacing:.16em}.setup-card p{color:var(--muted);font-size:13px;line-height:1.5}.setup-card a{color:var(--accent)}.setup-card input{width:100%;margin:8px 0 12px}.setup-card button{width:100%}#drawer{position:fixed;z-index:30;right:18px;top:50%;transform:translateY(-50%);width:min(390px,calc(100vw - 72px));height:min(72vh,640px);border-radius:24px;display:flex;flex-direction:column;overflow:hidden;transition:transform .3s ease,opacity .3s ease;box-shadow:0 18px 55px rgba(0,0,0,.45)}#drawer.closed{transform:translate(calc(100% + 18px),-50%);opacity:.98}.chat-tab{position:absolute;z-index:31;right:100%;top:50%;transform:translateY(-50%);width:44px;height:84px;border:1px solid var(--border);border-right:0;border-radius:18px 0 0 18px;background:var(--glass);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);display:flex;align-items:center;justify-content:center;color:#fff;cursor:pointer;font-size:24px;box-shadow:-10px 0 35px rgba(0,0,0,.25)}.chat-head{padding:11px 15px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;font-size:11px;letter-spacing:.12em;color:var(--muted)}#messages{flex:1;overflow:auto;padding:12px 15px;display:flex;flex-direction:column;gap:8px}.msg{max-width:78%;padding:8px 11px;border-radius:13px;font-size:13px;line-height:1.4;white-space:pre-wrap}.msg.u{align-self:flex-end;background:rgba(255,255,255,.10)}.msg.j{align-self:flex-start;background:rgba(0,0,0,.25);border:1px solid var(--border)}#inputbar{display:flex;gap:8px;padding:10px;border-top:1px solid var(--border)}#inputbar input{flex:1;min-width:0}.icon{width:42px}.hidden{display:none!important}#handcam{position:fixed;z-index:25;left:18px;bottom:245px;width:150px;height:112px;object-fit:cover;transform:scaleX(-1);border-radius:15px;opacity:.32;border:1px solid var(--border)}#pointer{position:fixed;z-index:26;width:18px;height:18px;border:2px solid var(--accent);border-radius:50%;pointer-events:none;box-shadow:0 0 18px var(--accent);display:none;transform:translate(-50%,-50%)}#photoPreview{max-width:100%;max-height:120px;border-radius:12px;margin-top:8px;display:none}.side-settings{position:fixed;z-index:35;right:18px;top:18px;width:44px;height:44px;border:1px solid var(--border);border-radius:14px;background:var(--glass);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);display:flex;align-items:center;justify-content:center;color:#fff;cursor:pointer;font-size:19px}.side-settings:hover,.chat-tab:hover{background:rgba(255,255,255,.11)}.tip{position:fixed;z-index:10;left:50%;top:50%;transform:translate(-50%,-50%);font-size:11px;color:var(--muted);pointer-events:none;opacity:.7}
.library-panel{position:fixed;z-index:45;right:18px;top:70px;width:min(420px,calc(100vw - 36px));max-height:calc(100vh - 100px);overflow:auto;border-radius:20px;padding:16px;display:none}.library-panel.open{display:block}.library-tabs{display:flex;gap:6px;margin-bottom:12px}.library-tabs .btn.active{background:rgba(255,255,255,.15)}.library-list{display:grid;gap:8px}.note-item{padding:10px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.04);font-size:12px;white-space:pre-wrap}.photo-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.photo-card{position:relative;border:1px solid var(--border);border-radius:12px;overflow:hidden;aspect-ratio:1;background:rgba(255,255,255,.04)}.photo-card img{width:100%;height:100%;object-fit:cover}.photo-delete{position:absolute;right:5px;top:5px;width:28px;height:28px;border:0;border-radius:50%;background:rgba(0,0,0,.7);color:#fff;cursor:pointer}.chat-toggle-fixed{position:fixed;z-index:50;right:0;top:50%;transform:translateY(-50%);width:46px;height:86px;border:1px solid var(--border);border-right:0;border-radius:18px 0 0 18px;background:var(--glass);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);display:flex;align-items:center;justify-content:center;color:#fff;cursor:pointer;font-size:27px;box-shadow:-10px 0 35px rgba(0,0,0,.25)}.chat-toggle-fixed:hover{background:rgba(255,255,255,.12)}#drawer{right:58px}#drawer.closed{transform:translate(calc(100% + 58px),-50%)}@media(max-width:700px){.panel{left:60px;width:calc(100vw - 78px)}#drawer{right:50px;width:calc(100vw - 68px)}.chat-toggle-fixed{width:42px}.photo-grid{grid-template-columns:repeat(2,1fr)}}
.music-panel{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);width:min(760px,calc(100vw - 32px));padding:16px;z-index:30;display:none;opacity:0;transition:.18s ease}
.music-panel.open{display:block;opacity:1;transform:translate(-50%,-50%) scale(1)}
.music-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.music-title{font-weight:700;letter-spacing:.08em}
.music-frame{position:relative;width:100%;aspect-ratio:16/9;background:#05070a;border-radius:14px;overflow:hidden}
.music-frame iframe{width:100%;height:100%;border:0;display:block}
.music-note{margin-top:10px}

.about-panel{position:fixed;z-index:50;left:78px;top:70px;width:min(360px,calc(100vw - 96px));padding:16px;border-radius:20px;display:none;opacity:0;transform:translateX(-10px);transition:.18s ease}.about-panel.open{display:block;opacity:1;transform:translateX(0)}.camera-panel,.developer-panel,.usage-panel{position:fixed;z-index:50;right:18px;top:70px;width:min(520px,calc(100vw - 36px));max-height:calc(100vh - 100px);overflow:auto;border-radius:20px;padding:16px;display:none}.camera-panel.open,.developer-panel.open,.usage-panel.open{display:block}.developer-preview{position:fixed;z-index:60;inset:24px;display:none;border-radius:20px;overflow:hidden}.developer-preview.open{display:flex;flex-direction:column}.developer-preview iframe{width:100%;height:100%;border:0;background:#fff}.developer-preview .preview-head{flex:none;display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--glass);border-bottom:1px solid var(--border)}.camera-panel video{width:100%;max-height:45vh;object-fit:cover;border-radius:14px;background:#000;transform:scaleX(-1)}.developer-panel textarea{resize:vertical}.developer-panel select,.developer-panel input{min-width:0}.dev-output{max-height:180px;overflow:auto;white-space:pre-wrap;background:rgba(0,0,0,.25);border:1px solid var(--border);padding:10px;border-radius:12px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.dev-file{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)}.dev-file button{flex:none}.jarvis-core-hint{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2;pointer-events:none;text-align:center;color:rgba(255,255,255,.58);font-size:9px;letter-spacing:.28em;text-transform:uppercase;opacity:.75}.settings-divider{height:1px;background:var(--border);margin:12px 0}.voice-reactive{transform-origin:center center;will-change:transform,filter}</style></head><body>
<div id="scene"></div><div id="top"><div class="brand"><i></i>JARVIS</div><div class="status" id="status">INITIALIZING</div></div><div class="tip" id="tip">Move your hand • pinch to select • open palm to zoom</div><video id="handcam" playsinline></video><div id="pointer"></div>
<div class="left-rail" aria-label="Jarvis tools">
<button class="rail-btn" id="settingsBtn" aria-label="Open settings" title="Settings">⚙</button>
<button class="rail-btn" id="libraryBtn" aria-label="Open Jarvis storage" title="Jarvis storage">▣</button>
<button class="rail-btn" id="aboutBtn" aria-label="About Jarvis" title="About Jarvis">ⓘ</button>
</div>
<button class="chat-toggle-fixed" id="chatToggle" aria-label="Open chat" title="Open chat">‹</button>
<div class="panel glass" id="settings"><div class="title">Settings</div><div class="switch"><span class="small">Accent colour</span><input id="accent" type="color" value="#46e0d0"></div><div class="switch"><span class="small">Hand camera preview</span><button class="btn" id="camToggle">Hide</button></div><div class="switch"><span class="small">Voice replies</span><button class="btn" id="voiceToggle">On</button></div><div class="switch"><span class="small">Continuous voice chat</span><button class="btn" id="continuousVoiceBtn">Off</button></div><div class="switch"><span class="small">Jarvis voice</span><select id="voiceSelect" class="field" style="flex:1.4"></select></div><div class="row"><button class="btn" id="customVoiceBtn">Add your own voice</button><button class="btn" id="removeVoiceBtn">Remove</button></div><div id="customVoiceForm" class="hidden"><input id="elevenKey" class="field" type="password" placeholder="ElevenLabs API key"><input id="elevenVoiceId" class="field" placeholder="ElevenLabs Voice ID"><div class="row"><button class="btn" id="saveVoiceBtn">Save voice</button><button class="btn" id="testVoiceBtn">Test</button></div></div><div class="row"><button class="btn" id="usageBtn">API usage</button><button class="btn" id="developerBtn">Developer</button><button class="btn" id="cameraBtn">Camera</button></div><div class="settings-divider"></div><div class="title" style="font-size:13px">AI provider switcher</div><div class="small">Groq stays the default. Optional providers can be added and Jarvis will automatically fall back when the active provider is unavailable or out of quota.</div><div class="switch"><span class="small">Provider</span><select id="aiProvider" class="field" style="flex:1.4"><option value="groq">Groq (default)</option><option value="openai">OpenAI / ChatGPT API</option><option value="openrouter">OpenRouter</option><option value="xai">xAI / Grok</option><option value="gemini">Google Gemini</option></select></div><input id="aiProviderKey" class="field" type="password" placeholder="Optional provider API key"><div class="row"><button class="btn" id="saveAiProvider">Save / switch</button><button class="btn" id="aiProviderStatus">Status</button></div><div class="small" id="aiProviderInfo">Loading provider status…</div><div class="settings-divider"></div><div class="title" style="font-size:13px">Roborock / Smart Home</div><div class="small">Optional. Configure later through Home Assistant. Credentials stay on the server.</div><input id="haUrl" class="field" placeholder="Home Assistant URL (https://...)"><input id="haToken" class="field" type="password" placeholder="Home Assistant long-lived access token"><input id="roborockEntity" class="field" placeholder="Roborock entity ID (e.g. vacuum.roborock)"><div class="row"><button class="btn" id="saveSmartHome">Save bridge</button><button class="btn" id="testSmartHome">Test</button></div><div class="small" id="smartHomeStatus">Not configured.</div><div class="switch"><span class="small">Voice expression</span><select id="expressionMode" class="field" style="flex:1.4"><option value="auto">Auto</option><option value="strong">Strong</option><option value="subtle">Subtle</option></select></div><div class="row"><button class="btn" id="resetView">Reset view</button><button class="btn" id="clearWords">Clear words</button></div><div class="switch"><span class="small">Reduce lag</span><button class="btn" id="reduceLagBtn">Off</button></div><div class="small">Settings are saved to Jarvis memory when Firebase is connected.</div></div>
<div class="about-panel glass" id="aboutPanel"><div class="music-head"><div><div class="title">About Jarvis</div><div class="small">Performance information</div></div><button class="btn" id="aboutClose">Close</button></div><div class="small">Made by Markus 2026</div><div class="small" style="margin-top:8px">FPS: <b id="fpsValue">--</b></div><div class="small" style="margin-top:6px">Status: <b id="lagValue">Checking…</b></div><div class="small" style="margin-top:10px">Jarvis knows Markus is the creator.</div><div class="small" id="firebaseUsage" style="margin-top:10px">Firebase storage: Checking…</div></div><div class="camera-panel glass" id="cameraPanel"><div class="music-head"><div><div class="title">Jarvis Camera</div><div class="small">The preview stays on this device until you ask Jarvis to analyze it.</div></div><button class="btn" id="cameraClose">Close</button></div><video id="cameraVideo" autoplay playsinline muted></video><div class="row"><button class="btn" id="cameraStart">Start camera</button><button class="btn" id="cameraAnalyze">Analyze frame</button></div><div class="small" id="cameraStatus">Camera is off.</div></div><div class="developer-panel glass" id="developerPanel"><div class="music-head"><div><div class="title">Developer</div><div class="small">Code, files, tests, and downloads in the Jarvis workspace.</div></div><button class="btn" id="developerClose">Close</button></div><div class="row"><select class="field" id="devLanguage"><option>HTML</option><option>CSS</option><option>JavaScript</option><option>Python</option><option>TypeScript</option><option>Java</option><option>C</option><option>C++</option><option>C#</option><option>Go</option><option>Rust</option><option>Swift</option><option>SQL</option><option>Bash</option><option>JSON</option></select><input class="field" id="devFilename" placeholder="index.html"></div><textarea class="field" id="devPrompt" placeholder="Describe the code you want..." style="width:100%;min-height:75px"></textarea><div class="row"><button class="btn" id="devGenerate">Generate</button><button class="btn" id="devSave">Save file</button><button class="btn" id="devCopy">Copy</button><button class="btn" id="devDownload">Download</button><button class="btn" id="devZip">ZIP</button></div><textarea class="field" id="devCode" placeholder="Clean source code appears here..." style="width:100%;min-height:220px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace"></textarea><div class="row"><input class="field" id="devCommand" placeholder="Command (workspace)" value="python --version"><button class="btn" id="devRun">Run</button></div><div class="small" id="devRunHint">HTML, CSS, and JavaScript use Live Preview. Other languages use the workspace command.</div><pre class="dev-output" id="devOutput"></pre><div class="title">Workspace files</div><div id="devFiles" class="library-list"></div></div><div class="developer-preview glass" id="developerPreview"><div class="preview-head"><div><div class="title" style="margin:0">Live Preview</div><div class="small" id="previewStatus">HTML/CSS/JavaScript</div></div><button class="btn" id="previewClose">Close</button></div><iframe id="previewFrame" sandbox="allow-scripts allow-forms allow-modals allow-popups"></iframe></div><div class="usage-panel glass" id="usagePanel"><div class="music-head"><div><div class="title">API Usage</div><div class="small">Usage reported by the configured providers or tracked from completed requests.</div></div><button class="btn" id="usageClose">Close</button></div><div id="usageContent" class="small">Loading…</div></div><div class="music-panel glass" id="musicPanel"><div class="music-head"><div><div class="title">YouTube Music</div><div class="music-title" id="musicTitle">Ready</div></div><button class="btn" id="musicClose" aria-label="Close music player">Close</button></div><div class="music-frame"><iframe id="musicFrame" title="YouTube player" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen playsinline></iframe></div><div class="small music-note" id="musicStatus">Finding a video…</div></div>
<div class="library-panel glass" id="library"><div class="title">My Jarvis Storage</div><div class="library-tabs"><button class="btn active" id="photosTab">Photos</button><button class="btn" id="notesTab">Notes</button><button class="btn" id="memoryTab">Memory</button></div><div id="photosView"><div class="row"><button class="btn" id="uploadPhoto">Add photo</button><span class="small">Saved to Firebase when connected</span></div><input id="libraryPhotoInput" class="hidden" type="file" accept="image/*"><div class="photo-grid" id="photoGrid"></div></div><div id="notesView" class="hidden"><div class="row"><input class="field" id="noteInput" placeholder="Write a note..."><button class="btn" id="saveNote">Save</button></div><div class="row"><button class="btn" id="clearNotes">Clear notes</button></div><div class="library-list" id="notesList"></div></div><div id="memoryView" class="hidden"><div class="small">Jarvis stores your recent conversation in Firebase when connected, so it can be restored after Render restarts.</div><div class="row"><button class="btn" id="clearMemory">Clear conversation memory</button></div></div></div>
<div id="drawer" class="glass"><div class="chat-head"><span>JARVIS CHAT</span><span><button class="btn" id="musicOpenBtn">Music</button><button class="btn" id="photoBtn">Analyze photo</button></span></div><div id="messages"></div><div id="inputbar"><input class="field" id="text" placeholder="Talk to Jarvis..." autocomplete="off"><button class="btn icon" id="mic"></button><button class="btn icon" id="send"></button></div><input id="photoInput" class="hidden" type="file" accept="image/*"><img id="photoPreview" alt="Selected photo"></div>
<div id="setup" class="setup"><div class="setup-card glass"><h1>● JARVIS</h1><p>Add your Groq API key before starting. Nothing is embedded in this page. You can choose whether the server remembers it under <code>~/.jarvis/</code>.</p><input class="field" id="key" type="password" placeholder="gsk_..." autocomplete="off"><label class="small"><input id="save" type="checkbox" checked> Save key to this device</label><button class="btn" id="begin">Initialize Jarvis</button><div class="small" id="err"></div></div></div>
<script>
const $=id=>document.getElementById(id), sceneEl=$('scene'), messages=$('messages');
const MIC_ICON='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
const STOP_ICON='<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
const SEND_ICON='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
$('mic').innerHTML=MIC_ICON;$('send').innerHTML=SEND_ICON;let scene,camera,renderer,fireworks,jarvis,handPointer=null,handSeen=false,pinchLatch=false,targetZ=34,currentZ=34,voiceOn=localStorage.getItem('jarvisVoice')!=='off',camVisible=true,accent=localStorage.getItem('jarvisAccent')||'#46e0d0',words=[],wordSprites=[],selected=null;
let fpsValue=0,fpsFrames=0,fpsWindowStart=performance.now();
let reduceLag=localStorage.getItem('jarvisReduceLag')==='1';
let selectedVoiceURI=localStorage.getItem('jarvisVoiceURI')||'',availableVoices=[];
function loadVoices(){
 availableVoices=('speechSynthesis' in window)?speechSynthesis.getVoices():[];
 const sel=$('voiceSelect');if(!sel)return;
 sel.innerHTML='<option value="">Default</option>'+availableVoices.map(v=>`<option value="${v.voiceURI}">${v.name} (${v.lang})</option>`).join('');
 sel.value=(selectedVoiceURI&&availableVoices.some(v=>v.voiceURI===selectedVoiceURI))?selectedVoiceURI:'';
}
if('speechSynthesis' in window){loadVoices();speechSynthesis.onvoiceschanged=loadVoices}
$('voiceSelect').onchange=e=>{selectedVoiceURI=e.target.value;localStorage.setItem('jarvisVoiceURI',selectedVoiceURI);saveRemoteSettings()};
$('accent').value=accent;document.documentElement.style.setProperty('--accent',accent);
async function loadRemoteSettings(){
  loadingRemoteSettings=true;
  try{
    const r=await fetch('/api/settings'); const d=await r.json(); const s=d.settings||{};
    if(s.accent){accent=s.accent;localStorage.setItem('jarvisAccent',accent);$('accent').value=accent;document.documentElement.style.setProperty('--accent',accent)}
    if(typeof s.voiceOn==='boolean'){voiceOn=s.voiceOn;localStorage.setItem('jarvisVoice',voiceOn?'on':'off')}
    if(typeof s.continuousVoice==='boolean'){continuousVoice=s.continuousVoice;localStorage.setItem('jarvisContinuousVoice',continuousVoice?'on':'off')}
    if(s.voiceURI!=null){selectedVoiceURI=s.voiceURI;localStorage.setItem('jarvisVoiceURI',selectedVoiceURI)}
    if(s.voiceMode){localStorage.setItem('jarvisVoiceMode',s.voiceMode)}
    if(s.expressionMode){localStorage.setItem('jarvisExpression',s.expressionMode)}
    if(typeof s.reduceLag==='boolean')localStorage.setItem('jarvisReduceLag',s.reduceLag?'1':'0');
    if(typeof s.chatOpen==='boolean')localStorage.setItem('jarvisChatOpen',s.chatOpen?'1':'0');
    if(typeof s.camVisible==='boolean')camVisible=s.camVisible;
    reduceLag=localStorage.getItem('jarvisReduceLag')==='1';
    chatOpen=localStorage.getItem('jarvisChatOpen')!=='0';
    if($('handcam'))$('handcam').classList.toggle('hidden',!camVisible);
    if($('camToggle'))$('camToggle').textContent=camVisible?'Hide':'Show';
    if($('expressionMode')&&s.expressionMode)$('expressionMode').value=s.expressionMode;
    applyReduceLag(reduceLag);setChatOpen(chatOpen);
    if($('voiceToggle'))$('voiceToggle').textContent=voiceOn?'On':'Off';
    if($('continuousVoiceBtn'))$('continuousVoiceBtn').textContent=continuousVoice?'On':'Off';
  }catch(e){}
  finally{loadingRemoteSettings=false}
}
let settingsSaveTimer=null,loadingRemoteSettings=false;
function collectSettings(){
  return {accent,voiceOn,continuousVoice,voiceURI:selectedVoiceURI,voiceMode:localStorage.getItem('jarvisVoiceMode')||'browser',
          expressionMode:$('expressionMode')?.value||localStorage.getItem('jarvisExpression')||'auto',
          reduceLag,chatOpen,camVisible};
}
function saveRemoteSettings(){
  if(loadingRemoteSettings)return;
  clearTimeout(settingsSaveTimer);
  settingsSaveTimer=setTimeout(()=>fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:collectSettings()})}).catch(()=>{}),150);
}

function addMsg(role,text){
 let d=document.createElement('div');d.className='msg '+(role==='user'?'u':'j');d.textContent=text;messages.appendChild(d);messages.scrollTop=messages.scrollHeight;
 if(role!=='user'&&window.MathJax?.typesetPromise){window.MathJax.typesetPromise([d]).catch(()=>{})}
}
async function loadSavedChat(){try{const r=await fetch('/api/chat');const d=await r.json();messages.innerHTML='';(d.messages||[]).forEach(m=>addMsg(m.role==='user'?'user':'j',m.content||''))}catch(e){}}
function showVoiceStatus(t){$('status').textContent=t}
let speechRecognition=null, listening=false, isJarvisSpeaking=false, jarvisAudio=null;
let voiceLevel=0, voiceTarget=0, voiceAnalyser=null, voiceAudioContext=null, voiceSource=null, voiceEnvelopeTimer=null;
let continuousVoice=localStorage.getItem('jarvisContinuousVoice')==='on';
function splitSentences(text){return (text.match(/[^.!?]+[.!?]*\\s*|[^.!?]+$/g)||[text]).map(s=>s.trim()).filter(Boolean)}
function cleanSpeechText(text){
 return String(text||'')
   .replace(/```[\s\S]*?```/g,'Code omitted from speech.')
   .replace(/`([^`]+)`/g,'$1')
   .replace(/https?:\/\/\S+/g,'the linked page')
   .replace(/\[\[(.*?)\]\]/gs,'$1')
   .replace(/\\\((.*?)\\\)/gs,'$1')
   .replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g,'$1 divided by $2')
   .replace(/\\sqrt\{([^{}]+)\}/g,'square root of $1')
   .replace(/\^\{([^{}]+)\}/g,' to the power of $1')
   .replace(/\s{2,}/g,' ')
   .trim();
}
function beginVoiceMeterFromAudio(audio){
 try{
   voiceAudioContext=voiceAudioContext||new (window.AudioContext||window.webkitAudioContext)();
   if(voiceAudioContext.state==='suspended')voiceAudioContext.resume().catch(()=>{});
   if(voiceSource){try{voiceSource.disconnect()}catch(e){}}
   voiceAnalyser=voiceAudioContext.createAnalyser();voiceAnalyser.fftSize=256;voiceAnalyser.smoothingTimeConstant=.72;
   voiceSource=voiceAudioContext.createMediaElementSource(audio);voiceSource.connect(voiceAnalyser);voiceAnalyser.connect(voiceAudioContext.destination);
   const data=new Uint8Array(voiceAnalyser.frequencyBinCount);
   const meter=()=>{if(!voiceAnalyser||audio.paused){voiceTarget=0;return}voiceAnalyser.getByteFrequencyData(data);let sum=0;for(let i=0;i<data.length;i++)sum+=data[i];voiceTarget=Math.min(1,sum/(data.length*255)*2.2);requestAnimationFrame(meter)};meter();
 }catch(e){voiceAnalyser=null;voiceTarget=.35}
}
function beginSyntheticVoiceMeter(text){
 clearInterval(voiceEnvelopeTimer);
 let i=0;voiceTarget=.18;
 voiceEnvelopeTimer=setInterval(()=>{
   if(!isJarvisSpeaking){clearInterval(voiceEnvelopeTimer);voiceTarget=0;return}
   const ch=text[Math.min(i++,text.length-1)]||' ';
   voiceTarget=Math.min(1,.18+(/[aeiou]/i.test(ch)?Math.random()*.48:.12+Math.random()*.22));
   if(i>=text.length)i=0;
 },38);
}
function stopJarvisSpeech(){
 if('speechSynthesis' in window) window.speechSynthesis.cancel();
 if(jarvisAudio){try{jarvisAudio.pause();jarvisAudio.currentTime=0}catch(e){}try{jarvisAudio.src=''}catch(e){}jarvisAudio=null}
 isJarvisSpeaking=false;
 voiceTarget=0;voiceLevel=0;clearInterval(voiceEnvelopeTimer);
 if(jarvis)jarvis.userData.talking=false;
 showVoiceStatus('JARVIS LISTENING');
}
function detectEmotion(text){
 const t=(text||'').toLowerCase();
 if(/[!?]{2,}/.test(text)||/\b(what|no way|oh my|wow|incredible|unbelievable|shocked|seriously)\b/.test(t)) return 'shocked';
 if(/\b(angry|mad|furious|annoyed|ridiculous|damn|hate)\b/.test(t)) return 'angry';
 if(/\b(sad|sorry|unfortunately|regret|miss|upset|bad news)\b/.test(t)) return 'sad';
 if(/\b(happy|great|awesome|excellent|glad|yay|wonderful|congratulations)\b/.test(t)) return 'happy';
 if(/\b(confused|confusing|unclear|not sure|huh|what do you mean|i don't understand)\b/.test(t)||/\?\s*$/.test(text)) return 'confused';
 if(/\b(surprise|surprised|wow|really)\b/.test(t)) return 'surprised';
 return 'neutral';
}
function emotionProfile(emotion){
 const mode=localStorage.getItem('jarvisExpression')||'auto';
 const strong=mode==='strong', subtle=mode==='subtle';
 const scale=strong?1:(subtle?.55:.82);
 const profiles={
  neutral:{pitch:1.00,rate:1.00,volume:.98},
  happy:{pitch:1.00+.38*scale,rate:1.02+.16*scale,volume:1.00},
  surprised:{pitch:1.00+.55*scale,rate:1.00+.08*scale,volume:1.00},
  shocked:{pitch:1.00+.62*scale,rate:.92+.02*scale,volume:1.00},
  confused:{pitch:1.00+.30*scale,rate:.84+.04*scale,volume:.96},
  angry:{pitch:1.00-.32*scale,rate:1.05+.12*scale,volume:1.00},
  sad:{pitch:1.00-.28*scale,rate:.84-.05*scale,volume:.86}
 };
 return profiles[emotion]||profiles.neutral;
}
function chooseNaturalVoice(emotion='neutral'){
 if(selectedVoiceURI){const exact=availableVoices.find(v=>v.voiceURI===selectedVoiceURI);if(exact)return exact}
 const english=availableVoices.filter(v=>/^en(-|_)/i.test(v.lang));
 const natural=english.filter(v=>/(natural|neural|premium|enhanced|siri|google|microsoft|ava|aria|jenny|guy|daniel|samantha|alex|karen|moira)/i.test(v.name));
 if(emotion==='angry'||emotion==='sad') return natural.find(v=>/male|guy|daniel|alex/i.test(v.name))||natural[0]||english[0]||availableVoices[0]||null;
 if(emotion==='happy'||emotion==='surprised'||emotion==='shocked') return natural.find(v=>/female|ava|aria|jenny|samantha|karen/i.test(v.name))||natural[0]||english[0]||availableVoices[0]||null;
 return natural[0]||english[0]||availableVoices[0]||null;
}
function speechClauses(text){
 return (text.match(/[^,;:!?]+[,;:!?]*/g)||[text]).map(x=>x.trim()).filter(Boolean);
}
function speakJarvis(text,callback){
 if(!voiceOn){if(callback)callback();return}
 const spoken=cleanSpeechText(text);
 if(!spoken){if(callback)callback();return}
 window.speechSynthesis.cancel();
 let clauses=speechClauses(spoken);if(!clauses.length)clauses=[spoken];
 let sentenceEmotion=detectEmotion(text),index=0;
 function finishSpeech(){
  isJarvisSpeaking=false;if(jarvis)jarvis.userData.talking=false;
  if(callback)callback();
  if(continuousVoice&&!listening){setTimeout(()=>startVoiceListening(),120)}
  else showVoiceStatus(handSeen?'GESTURE TRACKING ACTIVE':'ONLINE');
 }
 function speakNext(){
  if(index>=clauses.length){finishSpeech();return}
  const clause=clauses[index++];
  let emotion=detectEmotion(clause);if(emotion==='neutral')emotion=sentenceEmotion;
  const p=emotionProfile(emotion),voice=chooseNaturalVoice(emotion);
  const utter=new SpeechSynthesisUtterance(clause);
  if(voice)utter.voice=voice;
  const contour=(index%2===0?1:-1)*0.045;
  let pitch=p.pitch+contour;
  if(/[!?]$/.test(clause))pitch+=.08;
  if(emotion==='shocked'||emotion==='surprised')pitch+=.08;
  if(emotion==='angry')pitch-=.06;
  utter.pitch=Math.min(1.75,Math.max(.55,pitch+(Math.random()-.5)*.07));
  utter.rate=Math.min(1.34,Math.max(.68,p.rate+(Math.random()-.5)*.045));
  utter.volume=p.volume;
  utter.onstart=()=>{isJarvisSpeaking=true;if(jarvis)jarvis.userData.talking=true;beginSyntheticVoiceMeter(spoken);showVoiceStatus('JARVIS SPEAKING')};
  utter.onend=()=>{setTimeout(speakNext,emotion==='shocked'||emotion==='surprised'?70:35)};
  utter.onerror=()=>{if(index<clauses.length)setTimeout(speakNext,25);else finishSpeech()};
  window.speechSynthesis.speak(utter);
 }
 speakNext();
}
async function speak(text){
 if(!voiceOn)return;
 const spoken=cleanSpeechText(text);
 if(localStorage.getItem('jarvisVoiceMode')==='elevenlabs'){
  try{
   if(jarvisAudio){try{jarvisAudio.pause()}catch(e){}}
   const r=await fetch('/api/voice/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:spoken})});
   if(r.ok){
    const blob=await r.blob(),url=URL.createObjectURL(blob);
    jarvisAudio=new Audio(url);
    jarvisAudio.onplay=()=>{isJarvisSpeaking=true;if(jarvis)jarvis.userData.talking=true;beginVoiceMeterFromAudio(jarvisAudio);showVoiceStatus('JARVIS SPEAKING')};
    jarvisAudio.onended=()=>{URL.revokeObjectURL(url);jarvisAudio=null;isJarvisSpeaking=false;voiceTarget=0;if(jarvis)jarvis.userData.talking=false;if(continuousVoice&&!listening)setTimeout(()=>startVoiceListening(),120);else showVoiceStatus(handSeen?'GESTURE TRACKING ACTIVE':'ONLINE')};
    jarvisAudio.onerror=()=>{URL.revokeObjectURL(url);jarvisAudio=null;speakJarvis(spoken)};
    await jarvisAudio.play();return;
   }
  }catch(e){}
 }
 speakJarvis(spoken);
}
function canvasWord(text){let c=document.createElement('canvas'),x=c.getContext('2d'),size=64;c.width=512;c.height=128;x.font='600 42px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';x.textAlign='center';x.textBaseline='middle';x.fillStyle=accent;x.shadowColor=accent;x.shadowBlur=18;x.fillText(text,256,64);let t=new THREE.CanvasTexture(c);t.needsUpdate=true;return new THREE.SpriteMaterial({map:t,transparent:true,depthWrite:false})}
function makeWord(text,i){let s=new THREE.Sprite(canvasWord(text));s.userData.word=text;s.userData.angle=(i/Math.max(words.length,1))*Math.PI*2;s.userData.radius=13+(i%4)*3;s.userData.y=((i%5)-2)*2.3;s.scale.set(4.5,1.12,1);scene.add(s);wordSprites.push(s)}
function rebuildWords(text,focusWord=null){let clean=text.toLowerCase().replace(/[^a-z0-9 ]/g,' ').split(/\s+/).filter(w=>w.length>3&&!['that','this','with','from','have','your','about','what','when','where','which','would','could','there','they','them','just','into','please','jarvis'].includes(w));let uniq=[...new Set(clean)].slice(-22);uniq.unshift('web','photos','notes','schedule');uniq=[...new Set(uniq)].slice(0,26);words=uniq.slice();wordSprites.forEach(s=>scene.remove(s));wordSprites=[];uniq.forEach((w,i)=>makeWord(w,i));if(focusWord){let target=wordSprites.find(s=>s.userData.word===focusWord);if(target){selected=target;targetZ=13;wordSprites.forEach(w=>w.material.opacity=w===target?1:.18)}}}
function makeFireworks(){if(reduceLag){fireworks=null;return}let n=1700,g=new THREE.BufferGeometry(),p=new Float32Array(n*3);for(let i=0;i<n;i++){let a=Math.random()*Math.PI*2,b=Math.acos(2*Math.random()-1),r=8+Math.random()*24;p[i*3]=r*Math.sin(b)*Math.cos(a);p[i*3+1]=r*Math.sin(b)*Math.sin(a);p[i*3+2]=r*Math.cos(b)}g.setAttribute('position',new THREE.BufferAttribute(p,3));let m=new THREE.PointsMaterial({color:accent,size:.13,transparent:true,opacity:.75,depthWrite:false,blending:THREE.AdditiveBlending});fireworks=new THREE.Points(g,m);scene.add(fireworks)}
function makeJarvis(){
 let c=document.createElement('canvas');c.width=c.height=1024;let x=c.getContext('2d'),cx=512,cy=512;x.translate(cx,cy);let g=x.createRadialGradient(0,0,35,0,0,470);g.addColorStop(0,'rgba(255,255,255,.20)');g.addColorStop(.22,'rgba(70,224,208,.18)');g.addColorStop(.6,'rgba(0,0,0,.56)');g.addColorStop(1,'rgba(0,0,0,0)');x.fillStyle=g;x.fillRect(-512,-512,1024,1024);for(let r of [400,360,320]){x.strokeStyle=accent+(r===360?'cc':'45');x.lineWidth=r===360?7:2.5;x.beginPath();x.arc(0,0,r,0,Math.PI*2);x.stroke()}x.strokeStyle=accent;x.lineWidth=8;x.lineCap='round';for(let a of [[-.9,1.0],[2.05,4.05]]){x.beginPath();x.arc(0,0,410,a[0],a[1]);x.stroke()}x.fillStyle='rgba(3,9,15,.90)';x.beginPath();x.arc(0,0,286,0,Math.PI*2);x.fill();x.strokeStyle=accent;x.lineWidth=9;x.beginPath();x.arc(0,0,286,0,Math.PI*2);x.stroke();x.fillStyle='rgba(255,255,255,.98)';x.font='800 88px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';x.textAlign='center';x.textBaseline='middle';x.fillText('JARVIS',0,0);let tex=new THREE.CanvasTexture(c);tex.needsUpdate=true;let spr=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,depthWrite:false}));spr.scale.set(13.5,13.5,1);spr.userData.talking=false;spr.userData.baseScale=13.5;spr.userData.voiceLevel=0;spr.userData.phase=Math.random()*Math.PI*2;scene.add(spr);jarvis=spr;
}

function init(){scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(55,innerWidth/innerHeight,.1,1000);camera.position.z=currentZ;renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(reduceLag?1:Math.min(devicePixelRatio,2));renderer.setSize(innerWidth,innerHeight);sceneEl.appendChild(renderer.domElement);makeFireworks();makeJarvis();rebuildWords('web photos notes schedule');addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)})}
function setAccent(v){accent=v;localStorage.setItem('jarvisAccent',v);document.documentElement.style.setProperty('--accent',v);initSceneRefresh();saveRemoteSettings()}
function initSceneRefresh(){let texts=words.length?words.join(' '):'web photos notes schedule';scene.remove(fireworks);wordSprites.forEach(s=>scene.remove(s));scene.remove(jarvis);wordSprites=[];makeFireworks();makeJarvis();rebuildWords(texts)}
function screenToWorld(x,y,z=0){let v=new THREE.Vector3((x-.5)*2,-(y-.5)*2,.2).unproject(camera),dir=v.sub(camera.position).normalize(),d=(z-camera.position.z)/dir.z;return camera.position.clone().add(dir.multiplyScalar(d))}
function hitAt(x,y){let ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2(x*2-1,-(y*2-1)),camera);let hits=ray.intersectObjects(wordSprites,false);return hits[0]?.object||null}
function selectWord(s){if(!s)return;selected=s;targetZ=13;wordSprites.forEach(w=>w.material.opacity=w===s?1:.18);addMsg('j',`Looking into “${s.userData.word}”.`);sendSilent(`Look into the topic "${s.userData.word}" and give me useful information. You may use web search if appropriate.`)}
function openJarvisUrl(url){
 if(!url)return;
 // Use the browser's normal navigation so YouTube or any requested website opens directly.
 window.location.href=url;
}
async function playMusic(query){
 const q=(query||'').trim();
 if(!q)return;
 const panel=$('musicPanel'),frame=$('musicFrame'),title=$('musicTitle'),status=$('musicStatus');
 title.textContent=q;status.textContent='Finding the first available YouTube video…';
 panel.classList.add('open');
 frame.src='about:blank';
 try{
   const r=await fetch('/api/youtube-first?q='+encodeURIComponent(q));
   const d=await r.json();
   if(d.video_id){
     frame.src='https://www.youtube.com/embed/'+encodeURIComponent(d.video_id)+'?autoplay=1&playsinline=1&rel=0';
     status.textContent='Playing on YouTube inside Jarvis. If iOS blocks autoplay, tap Play in the player.';
   }else{
     status.textContent='I could not find a playable video. You can close this player and try another request.';
   }
 }catch(e){
   status.textContent='I could not reach YouTube right now.';
 }
}
$('musicClose').onclick=()=>{ $('musicFrame').src='about:blank'; $('musicPanel').classList.remove('open'); };
$('musicOpenBtn').onclick=()=>playMusic('Baby Shark');
async function sendSilent(text){setBusy(true);try{let r=await fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,performance:{fps:fpsValue,lagging:fpsValue>0&&fpsValue<45}})});let d=await r.json();setBusy(false);if(d.action?.type==='play_music')playMusic(d.action.query);else if(d.action?.type==='open_url')openJarvisUrl(d.action.url);else if(d.action?.type==='open_library'){openLibrary(d.action.tab||'photos')}if(d.reply){addMsg('j',d.reply);await speak(d.reply);rebuildWords(text+' '+d.reply);if(listening===false)showVoiceStatus('ONLINE')}}catch(e){setBusy(false);showVoiceStatus('VOICE ERROR');addMsg('j','I lost connection to the server.')}}
function setBusy(b){$('status').textContent=b?'THINKING':'ONLINE'}
async function send(){let t=$('text').value.trim();if(!t)return;$('text').value='';addMsg('user',t);let focus=(t.toLowerCase().match(/\b(?:about|on|for|into|regarding)\s+([a-z0-9_-]{4,})/)||[])[1]||t.toLowerCase().replace(/[^a-z0-9 ]/g,' ').split(/\s+/).find(w=>w.length>5&&!['please','could','would','jarvis','tell','about'].includes(w));rebuildWords(t,focus);await sendSilent(t)}
$('send').onclick=send;$('text').onkeydown=e=>{if(e.key==='Enter')send()};
let chatOpen=localStorage.getItem('jarvisChatOpen')!=='0';
function setChatOpen(open){chatOpen=open;$('drawer').classList.toggle('closed',!open);$('chatToggle').textContent=open?'›':'‹';$('chatToggle').title=open?'Close chat':'Open chat';$('chatToggle').setAttribute('aria-label',open?'Close chat':'Open chat');localStorage.setItem('jarvisChatOpen',open?'1':'0');saveRemoteSettings()}
$('chatToggle').onclick=()=>setChatOpen(!chatOpen);setChatOpen(chatOpen);
function setLeftPanel(which){const settings=$('settings'),library=$('library'),panel=which==='settings'?settings:library,wasOpen=panel.classList.contains('open');settings.classList.remove('open');library.classList.remove('open');document.body.classList.remove('left-panel-open');$('settingsBtn').classList.remove('active');$('libraryBtn').classList.remove('active');if(!wasOpen){panel.classList.add('open');document.body.classList.add('left-panel-open');$(which==='settings'?'settingsBtn':'libraryBtn').classList.add('active');}} $('settingsBtn').onclick=()=>setLeftPanel('settings');
async function loadFirebaseUsage(){
  try{
    const r=await fetch('/api/usage');const d=await r.json();const f=d.firebase||{};
    const gb=n=>n==null?'—':(n/1024/1024/1024).toFixed(3)+' GB';
    $('firebaseUsage').innerHTML=f.configured?
      `Firebase Storage: <b>${gb(f.storage_used_bytes)}</b> used · <b>${gb(f.storage_free_bytes)}</b> remaining in the 5 GB no-cost Blaze storage allowance.<br>Jarvis Firestore memory estimate: <b>${(f.firestore_memory_estimate_bytes/1024).toFixed(1)} KB</b> of the 1 GiB Firestore free stored-data quota.`:
      'Firebase: Not configured on this Render service.';
  }catch(e){$('firebaseUsage').textContent='Firebase usage unavailable.'}
}
$('aboutBtn').onclick=()=>{$('aboutPanel').classList.add('open');$('settings').classList.remove('open');$('library').classList.remove('open');loadFirebaseUsage()};
$('aboutClose').onclick=()=>$('aboutPanel').classList.remove('open');
function applyReduceLag(on){reduceLag=!!on;localStorage.setItem('jarvisReduceLag',reduceLag?'1':'0');saveRemoteSettings();if(renderer)renderer.setPixelRatio(reduceLag?1:Math.min(devicePixelRatio,2));if(reduceLag&&fireworks){scene.remove(fireworks);fireworks=null}else if(!reduceLag&&!fireworks)makeFireworks();$('reduceLagBtn').textContent=reduceLag?'On':'Off';saveRemoteSettings();}
$('reduceLagBtn').textContent=reduceLag?'On':'Off';$('reduceLagBtn').onclick=()=>applyReduceLag(!reduceLag);
$('accent').oninput=e=>setAccent(e.target.value);
$('voiceToggle').onclick=()=>{voiceOn=!voiceOn;localStorage.setItem('jarvisVoice',voiceOn?'on':'off');saveRemoteSettings();$('voiceToggle').textContent=voiceOn?'On':'Off';if(!voiceOn){continuousVoice=false;localStorage.setItem('jarvisContinuousVoice','off');$('continuousVoiceBtn').textContent='Off';stopJarvisSpeech()}};
$('voiceToggle').textContent=voiceOn?'On':'Off';
$('continuousVoiceBtn').textContent=continuousVoice?'On':'Off';
$('continuousVoiceBtn').onclick=()=>{continuousVoice=!continuousVoice;localStorage.setItem('jarvisContinuousVoice',continuousVoice?'on':'off');saveRemoteSettings();$('continuousVoiceBtn').textContent=continuousVoice?'On':'Off'};
$('expressionMode').value=localStorage.getItem('jarvisExpression')||'auto';$('expressionMode').onchange=e=>{localStorage.setItem('jarvisExpression',e.target.value);saveRemoteSettings()};
$('camToggle').onclick=()=>{$('handcam').classList.toggle('hidden');camVisible=!$('handcam').classList.contains('hidden');$('camToggle').textContent=camVisible?'Hide':'Show';saveRemoteSettings()};$('resetView').onclick=()=>{targetZ=34;wordSprites.forEach(s=>s.material.opacity=1)};$('clearWords').onclick=()=>rebuildWords('web photos notes schedule');
async function loadPhotos(){try{const r=await fetch('/api/photos');const d=await r.json();$('photoGrid').innerHTML='';(d.photos||[]).forEach(p=>{const card=document.createElement('div');card.className='photo-card';card.innerHTML=`<img src="${p.url}" loading="lazy"><button class="photo-delete" title="Delete">×</button>`;card.querySelector('button').onclick=async()=>{await fetch('/api/photos/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:p.name})});loadPhotos()};$('photoGrid').appendChild(card)})}catch(e){$('photoGrid').innerHTML='<div class="small">Could not load photos.</div>'}}
async function loadNotes(){try{const r=await fetch('/api/notes');const d=await r.json();$('notesList').innerHTML='';(d.notes||[]).forEach(n=>{const el=document.createElement('div');el.className='note-item';el.textContent=n;$('notesList').appendChild(el)})}catch(e){$('notesList').innerHTML='<div class="small">Could not load notes.</div>'}}
function showLibraryTab(tab){['photosView','notesView','memoryView'].forEach(id=>$(id).classList.toggle('hidden',id!==tab+'View'));['photosTab','notesTab','memoryTab'].forEach(id=>$(id).classList.toggle('active',id===tab+'Tab'));if(tab==='photos')loadPhotos();if(tab==='notes')loadNotes()}
function openLibrary(tab='photos'){
 $('settings').classList.remove('open');
 $('library').classList.add('open');
 document.body.classList.add('left-panel-open');
 $('settingsBtn').classList.remove('active');
 $('libraryBtn').classList.add('active');
 showLibraryTab(tab);
 loadNotes();
}
$('libraryBtn').onclick=()=>{const wasOpen=$('library').classList.contains('open');setLeftPanel('library');if(!wasOpen){showLibraryTab('photos');loadNotes()}};
$('photosTab').onclick=()=>showLibraryTab('photos');$('notesTab').onclick=()=>showLibraryTab('notes');$('memoryTab').onclick=()=>showLibraryTab('memory');
$('uploadPhoto').onclick=()=>$('libraryPhotoInput').click();
$('libraryPhotoInput').onchange=async e=>{const f=e.target.files[0];if(!f)return;const fd=new FormData();fd.append('photo',f);const r=await fetch('/api/photos/upload',{method:'POST',body:fd});const d=await r.json();if(!r.ok){alert(d.error||'Photo upload failed.');return}loadPhotos();$('library').classList.add('open');e.target.value=''};
$('saveNote').onclick=async()=>{const text=$('noteInput').value.trim();if(!text)return;const r=await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});if(r.ok){$('noteInput').value='';loadNotes()}};
$('noteInput').onkeydown=e=>{if(e.key==='Enter')$('saveNote').click()};$('clearNotes').onclick=async()=>{if(confirm('Clear all saved notes?')){await fetch('/api/notes',{method:'DELETE'});loadNotes()}};
$('clearMemory').onclick=async()=>{
  if(!confirm('Clear saved conversation memory?'))return;
  const r=await fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'clear chat'})});
  if(!r.ok){alert('Could not clear Firebase memory.');return}
  messages.innerHTML='';await loadSavedChat();addMsg('j','Cleared - what would you like to talk about?');
};
$('photoBtn').onclick=()=>$('photoInput').click();$('photoInput').onchange=async e=>{let f=e.target.files[0];if(!f)return;let fd=new FormData();fd.append('photo',f);let uploadResp=await fetch('/api/photos/upload',{method:'POST',body:fd}).catch(()=>null);let uploadData=uploadResp?await uploadResp.json().catch(()=>({})):{};let savedName=uploadData.name||'';let r=new FileReader();r.onload=async()=>{$('photoPreview').src=r.result;$('photoPreview').style.display='block';let q=prompt('What should Jarvis look for in this photo?','Describe and analyze this photo.');if(q===null)return;addMsg('user','[Photo] '+q);setBusy(true);try{let rr=await fetch('/api/photo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:r.result,question:q,saved_name:savedName})});let d=await rr.json();setBusy(false);addMsg('j',d.reply||d.error||'I could not analyze the photo.');if(d.reply){rebuildWords(q+' '+d.reply);speak(d.reply)}}catch(err){setBusy(false);addMsg('j','Photo analysis failed.')}};r.readAsDataURL(f)};
let mediaRecorder=null,voiceChunks=[],voiceFallback=false,voiceStream=null;
async function initJarvisVoice(){
 if(!navigator.mediaDevices?.getUserMedia){addMsg('j','This browser cannot access the microphone. Please use Chrome or Edge over HTTPS.');return false}
 try{if(!voiceStream) voiceStream=await navigator.mediaDevices.getUserMedia({audio:true})}catch(e){console.error(e);showVoiceStatus('MICROPHONE PERMISSION DENIED');addMsg('j','Please allow microphone access, then press the microphone button again.');return false}
 const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(SpeechRecognition){
   speechRecognition=new SpeechRecognition();
   speechRecognition.lang='en-US';
   speechRecognition.interimResults=true;
   speechRecognition.continuous=false;
   speechRecognition.maxAlternatives=1;
   speechRecognition.onstart=()=>{listening=true;$('mic').innerHTML=STOP_ICON;showVoiceStatus('JARVIS LISTENING')};
   speechRecognition.onresult=(event)=>{let finalText='',interimText='';for(let i=event.resultIndex;i<event.results.length;i++){const text=event.results[i][0].transcript;if(event.results[i].isFinal)finalText+=text;else interimText+=text}const display=(finalText||interimText).trim();if(display)$('text').value=display};
   speechRecognition.onend=()=>{listening=false;$('mic').innerHTML=MIC_ICON;const t=$('text').value.trim();if(!t){showVoiceStatus('ONLINE');return}showVoiceStatus('THINKING');send()};
   speechRecognition.onerror=(event)=>{listening=false;$('mic').innerHTML=MIC_ICON;console.warn('Speech recognition error:',event.error);if(event.error==='not-allowed'||event.error==='service-not-allowed')showVoiceStatus('MICROPHONE PERMISSION DENIED');else if(event.error==='no-speech')showVoiceStatus('NO SPEECH DETECTED');else showVoiceStatus('VOICE ERROR')};
   voiceFallback=false;
   return true;
 }
 if(!window.MediaRecorder){addMsg('j','Speech recognition is unavailable in this browser. Please use Chrome or Edge.');return false}
 voiceFallback=true;return true;
}
async function startFallbackRecording(){
 if(!voiceStream){try{voiceStream=await navigator.mediaDevices.getUserMedia({audio:true})}catch(e){showVoiceStatus('MICROPHONE PERMISSION DENIED');addMsg('j','Please allow microphone access, then press the microphone button again.');return}}
 voiceChunks=[];
 const preferred=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
 try{mediaRecorder=preferred?new MediaRecorder(voiceStream,{mimeType:preferred}):new MediaRecorder(voiceStream)}catch(e){addMsg('j','This browser could not start voice recording. Please use Chrome or Edge.');return}
 const mime=mediaRecorder.mimeType||preferred||'audio/webm';
 mediaRecorder.ondataavailable=e=>{if(e.data&&e.data.size)voiceChunks.push(e.data)};
 mediaRecorder.onstart=()=>{listening=true;$('mic').innerHTML=STOP_ICON;showVoiceStatus('JARVIS LISTENING')};
 mediaRecorder.onerror=()=>{listening=false;$('mic').innerHTML=MIC_ICON;showVoiceStatus('VOICE ERROR')};
 mediaRecorder.onstop=async()=>{
  listening=false;$('mic').innerHTML=MIC_ICON;showVoiceStatus('TRANSCRIBING');
  const blob=new Blob(voiceChunks,{type:mime});
  if(!blob.size){showVoiceStatus('NO SPEECH DETECTED');return}
  const fd=new FormData();fd.append('audio',blob,'voice.webm');
  try{
   const r=await fetch('/api/transcribe',{method:'POST',body:fd});
   const d=await r.json();
   if(!r.ok||!d.text)throw Error(d.error||'I could not understand that.');
   $('text').value=d.text;
   await send();
  }catch(e){console.error(e);showVoiceStatus('VOICE ERROR');addMsg('j',e.message||'Voice transcription failed.')}
 };
 mediaRecorder.start();
}
async function startVoiceListening(){
 if(!voiceOn)return;
 if(isJarvisSpeaking)stopJarvisSpeech();
 if(!speechRecognition&&!voiceFallback){let ok=await initJarvisVoice();if(!ok)return}
 if(listening)return;
 if(speechRecognition){$('text').value='';try{speechRecognition.start()}catch(e){console.warn(e);showVoiceStatus('VOICE START FAILED')}}else await startFallbackRecording();
}
$('mic').onclick=async()=>{
 if(isJarvisSpeaking){stopJarvisSpeech();setTimeout(()=>startVoiceListening(),60);return}
 if(listening){if(speechRecognition){try{speechRecognition.stop()}catch(e){}}else if(mediaRecorder?.state==='recording')mediaRecorder.stop();return}
 await startVoiceListening();
};
async function initHands(){let v=$('handcam'),hands=new Hands({locateFile:f=>`https://cdn.jsdelivr.net/npm/@mediapipe/hands/${f}`});hands.setOptions({maxNumHands:1,modelComplexity:1,minDetectionConfidence:.55,minTrackingConfidence:.55});hands.onResults(res=>{if(!res.multiHandLandmarks?.length){handSeen=false;$('pointer').style.display='none';return}handSeen=true;$('pointer').style.display='block';let h=res.multiHandLandmarks[0],x=1-h[8].x,y=h[8].y;$('pointer').style.left=(x*100)+'%';$('pointer').style.top=(y*100)+'%';let span=Math.hypot(h[0].x-h[12].x,h[0].y-h[12].y);targetZ=THREE.MathUtils.clamp(THREE.MathUtils.mapLinear(span,.18,.42,48,13),13,48);jarvis.position.set(0,0,0);let pinch=Math.hypot(h[8].x-h[4].x,h[8].y-h[4].y)<.055;if(pinch&&!pinchLatch){pinchLatch=true;let hit=hitAt(x,y);if(hit)selectWord(hit)}if(!pinch)pinchLatch=false});let cam=new Camera(v,{onFrame:async()=>hands.send({image:v}),width:640,height:480});cam.start()}
function animate(t){
 requestAnimationFrame(animate);
 let now=performance.now();fpsFrames++;
 if(now-fpsWindowStart>=500){
   fpsValue=Math.round(fpsFrames*1000/(now-fpsWindowStart));fpsFrames=0;fpsWindowStart=now;
   let f=$('fpsValue'),l=$('lagValue');if(f)f.textContent=fpsValue;if(l)l.textContent=fpsValue<45?'Lagging':'Not lagging'
 }
 currentZ+=(targetZ-currentZ)*.08;camera.position.z=currentZ;
 if(fireworks){fireworks.rotation.y+=.0007;fireworks.rotation.x=Math.sin(t*.00018)*.03}
 wordSprites.forEach((s,i)=>{
   let a=s.userData.angle+t*.00018*(1+(i%3)*.12),r=s.userData.radius;
   s.position.set(Math.cos(a)*r,s.userData.y+Math.sin(t*.001+i)*.35,Math.sin(a)*r);
   let pulse=1+Math.sin(t*.001+i)*.025;s.scale.set(4.5*pulse,1.12*pulse,1)
 });
 voiceLevel+=(voiceTarget-voiceLevel)*.18;
 if(!isJarvisSpeaking) voiceTarget*=.92;
 if(jarvis){
   jarvis.position.set(0,0,0);jarvis.rotation.set(0,0,0);
   let idle=1+Math.sin(t*.0014)*.012;
   let beat=1+voiceLevel*.32+Math.sin(t*.014+jarvis.userData.phase)*voiceLevel*.08;
   let s=idle*beat;
   jarvis.scale.set(jarvis.userData.baseScale*s,jarvis.userData.baseScale*s,1);
   jarvis.material.opacity=.9+Math.min(.1,voiceLevel*.12);
 }
 renderer.render(scene,camera)
}

async function setup(){let r=await fetch('/api/status').catch(()=>null);if(r){let d=await r.json();if(d.configured){$('setup').classList.add('hidden');addMsg('j','Hello. Jarvis is online. Speak with the microphone, type below, or use hand gestures.')}}}
$('begin').onclick=async()=>{let key=$('key').value.trim();if(!key){$('err').textContent='A Groq API key is required.';return}$('begin').disabled=true;try{let r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({groq_key:key,save:$('save').checked})});let d=await r.json();if(!d.ok)throw Error(d.error);$('setup').classList.add('hidden');addMsg('j','Jarvis initialized. Gesture control and chat are ready.')}catch(e){$('err').textContent=e.message||'Could not reach the server.';$('begin').disabled=false}};
init();initHands().catch(()=>{$('status').textContent='HAND CAMERA UNAVAILABLE'});loadRemoteSettings();setup();loadSavedChat();animate(0);

async function loadVoiceConfig(){try{const r=await fetch('/api/voice/config');const d=await r.json();if(d.configured){const opt=document.createElement('option');opt.value='__elevenlabs__';opt.textContent='My ElevenLabs voice';$('voiceSelect').appendChild(opt)}}catch(e){}}
loadVoiceConfig();
async function loadAiProviders(){try{const r=await fetch('/api/ai/providers');const d=await r.json();$('aiProvider').value=d.selected||'groq';$('aiProviderInfo').innerHTML='<b>Active:</b> '+(d.active||'groq')+'<br>'+(d.providers||[]).map(x=>x.label+(x.configured?' ✓':' — not configured')).join('<br>')}catch(e){$('aiProviderInfo').textContent='Could not load provider status.'}}
$('saveAiProvider').onclick=async()=>{try{const provider=$('aiProvider').value,key=$('aiProviderKey').value.trim();const r=await fetch('/api/ai/providers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,api_key:key})});const d=await r.json();if(!r.ok)throw Error(d.error||'Could not save provider.');$('aiProviderKey').value='';await loadAiProviders()}catch(e){alert(e.message)}};
$('aiProviderStatus').onclick=loadAiProviders;loadAiProviders();
$('customVoiceBtn').onclick=()=>$('customVoiceForm').classList.toggle('hidden');
$('saveVoiceBtn').onclick=async()=>{const api_key=$('elevenKey').value.trim(),voice_id=$('elevenVoiceId').value.trim();const r=await fetch('/api/voice/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key,voice_id})});const d=await r.json();if(!r.ok){alert(d.error||'Could not save voice.');return}localStorage.setItem('jarvisVoiceMode','elevenlabs');saveRemoteSettings();await loadVoiceConfig();$('voiceSelect').value='__elevenlabs__';alert('ElevenLabs voice saved locally.');};
$('removeVoiceBtn').onclick=async()=>{if(!confirm('Remove the saved ElevenLabs key and voice?'))return;await fetch('/api/voice/config',{method:'DELETE'});localStorage.removeItem('jarvisVoiceMode');saveRemoteSettings();[...$('voiceSelect').options].filter(o=>o.value==='__elevenlabs__').forEach(o=>o.remove());$('voiceSelect').value='';};
$('testVoiceBtn').onclick=()=>speak('This is my custom Jarvis voice.');
const originalVoiceChange=$('voiceSelect').onchange;$('voiceSelect').onchange=e=>{if(e.target.value==='__elevenlabs__')localStorage.setItem('jarvisVoiceMode','elevenlabs');else{localStorage.setItem('jarvisVoiceMode','browser');selectedVoiceURI=e.target.value;localStorage.setItem('jarvisVoiceURI',selectedVoiceURI)}saveRemoteSettings()};

async function openUsage(){['developerPanel','cameraPanel','musicPanel'].forEach(id=>$(id).classList.remove('open'));$('usagePanel').classList.add('open');try{const r=await fetch('/api/usage');const d=await r.json();const g=d.groq||{},e=d.elevenlabs||{};$('usageContent').innerHTML=`<b>Groq</b><br>Today: ${g.groq_requests||0} requests<br>Input tokens: ${g.groq_input_tokens||0}<br>Output tokens: ${g.groq_output_tokens||0}<br>Total tracked tokens: ${(g.groq_input_tokens||0)+(g.groq_output_tokens||0)}<br>Requests/min remaining: ${g.remaining_requests??'Not reported'}<br>Tokens/min remaining: ${g.remaining_tokens??'Not reported'}<br>Credits/min status: <b>${g.per_minute_status||'Not reported'}</b><br><br><b>ElevenLabs</b><br>${e.configured?(e.characters_remaining!=null?'Characters remaining: '+e.characters_remaining:'Configured; provider quota unavailable'):'Not configured'}`;}catch(e){$('usageContent').textContent='Could not load usage.'}}
$('usageBtn').onclick=openUsage;$('usageClose').onclick=()=>$('usagePanel').classList.remove('open');

let cameraStream=null;
$('cameraBtn').onclick=()=>{['settings','developerPanel','usagePanel','musicPanel'].forEach(id=>$(id).classList.remove('open'));$('cameraPanel').classList.add('open')};$('cameraClose').onclick=()=>{$('cameraPanel').classList.remove('open');if(cameraStream){cameraStream.getTracks().forEach(t=>t.stop());cameraStream=null;$('cameraVideo').srcObject=null;$('cameraStatus').textContent='Camera is off.'}};
$('cameraStart').onclick=async()=>{try{cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});$('cameraVideo').srcObject=cameraStream;$('cameraStatus').textContent='Camera preview is local to this browser.'}catch(e){$('cameraStatus').textContent='Camera permission was denied or unavailable.'}};
$('cameraAnalyze').onclick=async()=>{if(!$('cameraVideo').videoWidth){$('cameraStatus').textContent='Start the camera first.';return}const c=document.createElement('canvas');c.width=$('cameraVideo').videoWidth;c.height=$('cameraVideo').videoHeight;c.getContext('2d').drawImage($('cameraVideo'),0,0);const image=c.toDataURL('image/jpeg',.82);const q=prompt('What should Jarvis look for?','Describe what you see in the camera frame.');if(q===null)return;$('cameraStatus').textContent='Sending this frame for analysis…';try{const r=await fetch('/api/photo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image,question:q})});const d=await r.json();addMsg('j',d.reply||d.error||'I could not analyze the frame.');if(d.reply)speak(d.reply);$('cameraStatus').textContent='Frame analyzed.'}catch(e){$('cameraStatus').textContent='Analysis failed.'}};

async function loadSmartHomeConfig(){
 try{
   const r=await fetch('/api/smart-home/config');const d=await r.json();
   $('haUrl').value=d.home_assistant_url||'';$('roborockEntity').value=d.roborock_entity_id||'';
   $('smartHomeStatus').textContent=d.configured?'Bridge configured.':'Not configured.';
 }catch(e){$('smartHomeStatus').textContent='Could not load smart-home settings.'}
}
$('saveSmartHome').onclick=async()=>{
 const payload={home_assistant_url:$('haUrl').value.trim(),home_assistant_token:$('haToken').value.trim(),roborock_entity_id:$('roborockEntity').value.trim()};
 try{
   const r=await fetch('/api/smart-home/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
   const d=await r.json();if(!r.ok)throw Error(d.error||'Could not save bridge.');
   $('haToken').value='';$('smartHomeStatus').textContent=d.configured?'Bridge saved.':'Saved, but not fully configured.';
 }catch(e){$('smartHomeStatus').textContent=e.message}
};
$('testSmartHome').onclick=async()=>{
 $('smartHomeStatus').textContent='Testing Home Assistant…';
 try{
  const r=await fetch('/api/roborock/test',{method:'POST'});const d=await r.json();
  $('smartHomeStatus').textContent=d.ok?`Connected: ${d.friendly_name||$('roborockEntity').value} — state: ${d.state||'unknown'}`:(d.error||'Test failed.');
 }catch(e){$('smartHomeStatus').textContent='Test failed: '+e.message}
};
loadSmartHomeConfig();

async function loadDevFiles(){try{const r=await fetch('/api/developer/files');const d=await r.json();$('devFiles').innerHTML='';(d.files||[]).forEach(name=>{const row=document.createElement('div');row.className='dev-file';row.innerHTML=`<span>${name}</span><button class="btn">Open</button>`;row.querySelector('button').onclick=async()=>{const rr=await fetch('/api/developer/file?name='+encodeURIComponent(name));const x=await rr.json();if(rr.ok){$('devFilename').value=name;$('devCode').value=x.content}};$('devFiles').appendChild(row)})}catch(e){$('devFiles').textContent='Could not load workspace.'}}
$('developerBtn').onclick=()=>{['settings','cameraPanel','usagePanel','musicPanel','aboutPanel'].forEach(id=>$(id).classList.remove('open'));$('developerPanel').classList.add('open');loadDevFiles()};$('developerClose').onclick=()=>$('developerPanel').classList.remove('open');
$('devGenerate').onclick=async()=>{const promptText=$('devPrompt').value.trim();if(!promptText)return;setBusy(true);try{const r=await fetch('/api/developer/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:promptText,language:$('devLanguage').value})});const d=await r.json();if(!r.ok){alert(d.error||'Generation failed.');return}$('devCode').value=d.code||''}finally{setBusy(false)}};
$('devCopy').onclick=async()=>{await navigator.clipboard.writeText($('devCode').value);$('devCopy').textContent='Copied';setTimeout(()=>$('devCopy').textContent='Copy',1000)};
$('devSave').onclick=async()=>{const name=$('devFilename').value.trim();if(!name)return alert('Enter a filename.');const r=await fetch('/api/developer/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,content:$('devCode').value})});const d=await r.json();if(!r.ok)return alert(d.error||'Save failed.');loadDevFiles()};
$('devDownload').onclick=async()=>{const name=$('devFilename').value.trim();if(!name)return alert('Enter a filename.');const r=await fetch('/api/developer/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,content:$('devCode').value})});if(!r.ok){const d=await r.json();return alert(d.error||'Save failed.')}window.location.href='/api/developer/download?name='+encodeURIComponent(name)};
$('devZip').onclick=()=>{window.location.href='/api/developer/download-zip'};
$('devRun').onclick=async()=>{const lang=$('devLanguage').value.toLowerCase(),code=$('devCode').value,previewable=['html','css','javascript'].includes(lang);if(previewable){let src='';if(lang==='html'){src=code}else if(lang==='css'){src='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+code+'</style></head><body><h2>CSS Live Preview</h2><p>Edit the CSS and run again to preview it.</p></body></html>'}else{const safe=code.replace(new RegExp('<'+'/script','gi'),'<\\/script');src='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><h2>JavaScript Live Preview</h2><scr'+'ipt>'+safe+'<'+'/script></body></html>'}$('previewFrame').srcdoc=src;$('previewStatus').textContent=lang.toUpperCase()+' preview';$('developerPreview').classList.add('open');$('devOutput').textContent='Live preview running inside Jarvis.';return}const cmd=$('devCommand').value.trim();if(!cmd){$('devOutput').textContent='Enter a workspace command.';return}const r=await fetch('/api/developer/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});const d=await r.json();$('devOutput').textContent=d.error||`Exit code: ${d.returncode}\n\n${d.stdout||''}\n${d.stderr||''}`};
$('previewClose').onclick=()=>{$('developerPreview').classList.remove('open');$('previewFrame').srcdoc='';};
$('devLanguage').onchange=()=>{const l=$('devLanguage').value.toLowerCase();$('devRunHint').textContent=['html','css','javascript'].includes(l)?'This language uses Live Preview inside Jarvis.':'This language uses the workspace command to run.'};
</script></body></html>'''

if __name__ == "__main__":
    port=int(os.environ.get("PORT",8080))
    app.run(host="0.0.0.0",port=port,debug=False)
