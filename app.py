#!/usr/bin/env python3
'''JARVIS gesture HUD - local Flask app for Cloud Shell or any Python terminal.'''
import os, re, json, datetime, base64, uuid, subprocess, shlex, time, zipfile, html, threading
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
GROQ_MODEL = "openai/gpt-oss-120b"
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

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS Interface</title>
<style>
:root {
  --accent: #46e0d0;
  --bg: #05080d;
  --glass: rgba(12, 18, 27, 0.64);
  --border: rgba(255, 255, 255, 0.13);
  --muted: rgba(220, 230, 239, 0.58);
}
* { box-sizing: border-box; }
html, body {
  margin: 0; width: 100%; height: 100%; overflow: hidden;
  background: radial-gradient(circle at 50% 45%, #0b1820 0%, var(--bg) 58%);
  color: #fff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.glass {
  background: var(--glass);
  backdrop-filter: blur(22px);
  border: 1px solid var(--border);
  box-shadow: 0 12px 45px rgba(0, 0, 0, 0.35);
}

/* Initialization Boot Screen with Dynamic Theme Glow Loader */
#boot {
  position: fixed; inset: 0; z-index: 999;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: radial-gradient(circle, color-mix(in srgb, var(--accent) 15%, #03070b), #03070b 70%);
  transition: opacity 0.8s ease, visibility 0.8s ease;
}
#boot.done { opacity: 0; visibility: hidden; pointer-events: none; }

.boot-loader-ring {
  position: relative; width: 140px; height: 140px; border-radius: 50%;
  border: 2px solid transparent;
  border-top-color: var(--accent);
  box-shadow: 0 0 30px color-mix(in srgb, var(--accent) 50%, transparent),
              inset 0 0 15px color-mix(in srgb, var(--accent) 30%, transparent);
  animation: spinLoader 1.2s cubic-bezier(0.68, -0.55, 0.27, 1.55) infinite;
}
.boot-loader-ring::before {
  content: ""; position: absolute; inset: -12px; border-radius: 50%;
  border: 2px dashed color-mix(in srgb, var(--accent) 60%, transparent);
  animation: spinLoader 3s linear infinite reverse;
}
.boot-loader-ring::after {
  content: ""; position: absolute; inset: 12px; border-radius: 50%;
  border: 2px solid transparent;
  border-bottom-color: var(--accent);
  filter: drop-shadow(0 0 10px var(--accent));
  animation: spinLoader 0.8s linear infinite;
}
.boot-core {
  position: absolute; inset: 0; display: grid; place-items: center;
  font-size: 11px; font-weight: 700; letter-spacing: 0.3em; color: #fff;
  text-shadow: 0 0 12px var(--accent);
}
.boot-meta {
  margin-top: 30px; font-size: 10px; letter-spacing: 0.25em; color: var(--accent);
  text-transform: uppercase; text-shadow: 0 0 8px var(--accent);
}

@keyframes spinLoader { to { transform: rotate(360deg); } }
@keyframes breathe { 50% { transform: scale(1.04); opacity: 0.85; } }

/* Jarvis Core Orb */
#core {
  position: absolute; left: 50%; top: 50%; width: min(42vw, 430px); aspect-ratio: 1;
  transform: translate(-50%, -50%); pointer-events: none;
}
.ring {
  position: absolute; inset: 8%;
  border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
  border-radius: 50%;
  box-shadow: 0 0 34px color-mix(in srgb, var(--accent) 22%, transparent);
  animation: spinLoader 18s linear infinite;
}
.ring:nth-child(2) { inset: 17%; border-style: dashed; animation-duration: 11s; animation-direction: reverse; }
.ring:nth-child(3) { inset: 27%; animation-duration: 7s; }

.energy {
  position: absolute; inset: 35%; border-radius: 50%;
  background: radial-gradient(circle, #fff 0%, var(--accent) 12%, transparent 70%);
  filter: drop-shadow(0 0 28px var(--accent));
  animation: breathe 2.8s infinite;
}

#core.listening .ring { animation-duration: 4s; }
#core.thinking .ring { animation-duration: 2.2s; }
#core.thinking .energy {
  background: radial-gradient(circle, #fff4c7, #f3b53d 16%, transparent 70%);
  filter: drop-shadow(0 0 32px #f3b53d);
}
#core.speaking .wave { animation: pulse 0.18s alternate infinite; }
.wave {
  position: absolute; inset: 5%; border-radius: 50%;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 18%, transparent);
}
@keyframes pulse { to { transform: scale(1.12); } }

/* UI Overlay */
#top {
  position: fixed; top: 18px; left: 18px; right: 18px;
  display: flex; justify-content: space-between; z-index: 3;
}
.brand { letter-spacing: 0.18em; font-weight: 700; font-size: 14px; }
.brand i {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 15px var(--accent); margin-right: 8px;
}
.status { font-size: 11px; color: var(--muted); letter-spacing: 0.1em; }

#tools {
  position: fixed; bottom: 78px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 8px; padding: 8px 10px; border-radius: 999px; z-index: 3;
  color: rgba(255, 255, 255, 0.35); font-size: 9px; letter-spacing: 0.1em;
}
.tool.active { color: var(--accent); text-shadow: 0 0 12px var(--accent); }

#activity {
  position: fixed; right: 18px; top: 68px; width: 290px; padding: 12px; border-radius: 18px; z-index: 4;
}
.activity { font-size: 11px; color: var(--muted); padding: 7px; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }
.activity b { display: block; color: #fff; font-size: 9px; letter-spacing: 0.1em; }

#tip {
  position: fixed; left: 50%; bottom: 112px; transform: translateX(-50%);
  padding: 10px 14px; border-radius: 14px; opacity: 0; transition: opacity 0.3s; z-index: 5;
}
.show { opacity: 1 !important; }

#immersive {
  position: fixed; inset: 0; background: #03070b; z-index: 8; display: none;
}
.imm { display: block !important; animation: fadeIn 0.42s ease; }
.imm video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.35; }
.hud {
  position: absolute; inset: 0;
  border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
  box-shadow: inset 0 0 100px color-mix(in srgb, var(--accent) 9%, transparent);
}
#draw { position: absolute; inset: 0; width: 100%; height: 100%; }
.immTop { position: absolute; top: 18px; left: 22px; right: 22px; display: flex; justify-content: space-between; font-size: 11px; letter-spacing: 0.14em; }
.controls { position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%); display: flex; gap: 8px; }
.controls button, .demo { cursor: pointer; padding: 9px 12px; border-radius: 12px; border: 1px solid var(--border); background: rgba(4, 8, 12, 0.8); color: #fff; }
@keyframes fadeIn { from { opacity: 0; transform: scale(0.98); } to { opacity: 1; transform: scale(1); } }

#demo { position: fixed; left: 18px; bottom: 18px; z-index: 12; display: flex; gap: 6px; flex-wrap: wrap; }
</style>
</head>
<body>

<div id="boot">
  <div class="boot-loader-ring">
    <div class="boot-core">JARVIS</div>
  </div>
  <div class="boot-meta">INITIALIZING CORE SYSTEM ●</div>
</div>

<div id="core">
  <div class="ring"></div>
  <div class="ring"></div>
  <div class="ring"></div>
  <div class="wave"></div>
  <div class="energy"></div>
</div>

<div id="top">
  <div class="brand"><i></i>JARVIS</div>
  <div class="status" id="status">READY</div>
</div>

<div id="activity" class="glass">
  <div style="font-size:11px; letter-spacing:.12em; color:#fff; margin-bottom:7px">JARVIS ACTIVITY</div>
  <div class="activity"><b>CORE</b>System online</div>
  <div class="activity"><b>MEMORY</b>Firestore synced</div>
  <div class="activity"><b>VISION</b>Standby</div>
</div>

<div id="tools" class="glass">
  <span class="tool active">● GROQ</span>
  <span class="tool">● MEMORY</span>
  <span class="tool">● WEB</span>
  <span class="tool">● VISION</span>
  <span class="tool">● MIC</span>
</div>

<div id="tip" class="glass">JARVIS · Proactive tip: keeps camera processing local for ultra-fast tracking.</div>

<div id="immersive">
  <video autoplay muted playsinline></video>
  <canvas id="draw"></canvas>
  <div class="hud"></div>
  <div class="immTop">
    <span>JARVIS / IMMERSIVE MODE</span>
    <span>VISION ACTIVE · HAND TRACKING</span>
  </div>
  <div class="controls">
    <button id="vision">Vision</button>
    <button id="clear">Clear Canvas</button>
    <button id="exit">Exit</button>
  </div>
</div>

<div id="demo">
  <button class="demo" onclick="state('idle')">Idle</button>
  <button class="demo" onclick="state('listening')">Listening</button>
  <button class="demo" onclick="state('thinking')">Thinking</button>
  <button class="demo" onclick="state('speaking')">Speaking</button>
  <button class="demo" onclick="toggleImmersive()">Immersive</button>
  <button class="demo" onclick="tip()">Proactive Tip</button>
</div>

<script>
const core = document.getElementById('core'), status = document.getElementById('status'), imm = document.getElementById('immersive');
function state(s) {
  core.className = s;
  status.textContent = s.toUpperCase();
}
function toggleImmersive() { imm.classList.toggle('imm'); }
function tip() {
  const t = document.getElementById('tip');
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 5000);
}
document.getElementById('exit').onclick = () => imm.classList.remove('imm');
document.getElementById('clear').onclick = () => {
  const c = document.getElementById('draw');
  c.getContext('2d').clearRect(0, 0, c.width, c.height);
};
setTimeout(() => document.getElementById('boot').classList.add('done'), 1800);
</script>
</body>
</html>"""

# Optional Firebase/Firestore persistence.
_FIREBASE_TOKEN_CACHE = {"token": None, "expires_at": 0}
FIRESTORE_CONVERSATIONS = "jarvis_conversations"
FIRESTORE_MEMORIES = "jarvis_memories"
FIRESTORE_PHOTOS = "jarvis_photos"
FIRESTORE_PHOTO_CHUNKS = "jarvis_photo_chunks"
_MEMORY_CACHE = {"at":0.0,"items":None,"legacy":None}
_CHAT_CACHE = {"at":0.0,"items":None}
_PERSIST_EXECUTOR = None

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
    global _FIREBASE_TOKEN_CACHE
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

def _persist_executor():
    global _PERSIST_EXECUTOR
    if _PERSIST_EXECUTOR is None:
        from concurrent.futures import ThreadPoolExecutor
        _PERSIST_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-save")
    return _PERSIST_EXECUTOR

def _background(fn,*args,**kwargs):
    try:
        _persist_executor().submit(fn,*args,**kwargs)
    except Exception:
        pass

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
    if ok:
        item={"text":text,"user_text":str(user_text),"assistant_text":str(assistant_text),"source":source,"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z")}
        cached=_MEMORY_CACHE.get("items")
        if isinstance(cached,list):
            cached.append(item)
            _MEMORY_CACHE["items"]=cached[-1000:]
            _MEMORY_CACHE["at"]=time.time()
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
    now=time.time()
    cached=_CHAT_CACHE.get("items")
    if isinstance(cached,list) and now-_CHAT_CACHE.get("at",0)<60:
        return list(cached)
    current=[]
    for d in _firestore_list_collection(FIRESTORE_CONVERSATIONS,1000):
        f=_fs_doc_fields(d)
        if f.get("role") in ("user","assistant") and f.get("content") is not None:current.append({"role":f["role"],"content":f["content"],"created_at":f.get("created_at","")})
    merged=[];seen=set()
    for m in _legacy_memory_messages()+sorted(current,key=lambda x:x.get("created_at","")):
        key=(m.get("role"),m.get("content"))
        if key not in seen:seen.add(key);merged.append({"role":m.get("role"),"content":m.get("content")})
    merged=merged[-1000:]
    _CHAT_CACHE["items"]=merged;_CHAT_CACHE["at"]=now
    return merged

def firebase_clear_conversations():
    if not _firebase_service_account():return True
    ok=True
    for collection in (FIRESTORE_CONVERSATIONS,FIRESTORE_MEMORIES):
        for d in _firestore_list_collection(collection,1000):
            name=d.get("name","")
            if name and not _firestore_delete_document(collection,name.rsplit("/",1)[-1]):ok=False
    ok = _firebase_delete_legacy_memory() and ok
    _CHAT_CACHE["items"]=[];_CHAT_CACHE["at"]=time.time()
    _MEMORY_CACHE["items"]=[];_MEMORY_CACHE["legacy"]=[];_MEMORY_CACHE["at"]=time.time()
    return ok

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
        if trimmed:_background(_firebase_memory_save,trimmed)
        else:_background(_firebase_delete_legacy_memory)
    _CHAT_CACHE["items"]=list(trimmed);_CHAT_CACHE["at"]=time.time()
    try:
        with open(CHAT_FILE,"w",encoding="utf-8") as f:json.dump(trimmed,f,ensure_ascii=False,indent=2)
    except OSError:pass

def _persist_exchange_worker(user_text,assistant_text,source,extra):
    if not _firebase_service_account():return
    _firebase_new_message_save("user",user_text,source,extra)
    _firebase_new_message_save("assistant",assistant_text,source,extra)
    _firebase_memory_save_pair(user_text,assistant_text,source)

def persist_exchange(user_text,assistant_text,source="chat",extra=None):
    if _firebase_service_account():
        _background(_persist_exchange_worker,user_text,assistant_text,source,extra)

def _firebase_memory_save_pair(user_text,assistant_text,source="conversation"):
    return _firebase_longterm_save(user_text,assistant_text,source)

saved_chat=load_chat()
if _firebase_service_account():
    _CHAT_CACHE["items"]=list(saved_chat);_CHAT_CACHE["at"]=time.time()
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
for _legacy_key in list(runtime_cfg):
    if (_legacy_key.endswith("_provider_status") or _legacy_key.endswith("_provider_verified_at")
            or _legacy_key in ("ai_provider", "last_ai_provider")):
        runtime_cfg.pop(_legacy_key, None)
for _legacy_key in list(runtime_cfg):
    if _legacy_key.endswith("_key") and _legacy_key not in ("groq_key", "elevenlabs_key"):
        runtime_cfg.pop(_legacy_key, None)
runtime_cfg["ai_provider"] = "groq"
runtime_cfg["last_ai_provider"] = "groq"
save_config(runtime_cfg)

USAGE_FILE = os.path.join(JARVIS_DIR, "usage.json")
def _load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE,"r",encoding="utf-8") as f:return json.load(f)
        except (json.JSONDecodeError,OSError): pass
    return {"date":datetime.date.today().isoformat(),"groq_input_tokens":0,"groq_output_tokens":0,"groq_requests":0}

def _save_usage(u):
    with open(USAGE_FILE,"w",encoding="utf-8") as f:json.dump(u,f,indent=2)

def record_groq_usage(resp, started_at=None):
    u=_load_usage();today=datetime.date.today().isoformat()
    if u.get("date")!=today:
        u={"date":today,"groq_input_tokens":0,"groq_output_tokens":0,"groq_requests":0}
    try:
        usage=resp.json().get("usage") or {}
        u["groq_input_tokens"] += int(usage.get("prompt_tokens",usage.get("input_tokens",0)) or 0)
        u["groq_output_tokens"] += int(usage.get("completion_tokens",usage.get("output_tokens",0)) or 0)
    except Exception: pass
    u["groq_requests"] += 1
    u["last_http_status"] = int(resp.status_code)
    u["last_request_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z")
    if started_at is not None:
        u["last_latency_ms"] = round((time.perf_counter()-started_at)*1000)
    if resp.ok:
        u["last_result"]="success"
        u["last_success_at"] = u["last_request_at"]
    elif resp.status_code in (401,403):
        u["last_result"]="invalid_key"
    elif resp.status_code==402:
        u["last_result"]="quota_or_billing_rejected"
    elif resp.status_code==429:
        u["last_result"]="rate_limited"
    else:
        u["last_result"]=f"http_{resp.status_code}"
    for k in ("x-ratelimit-limit-requests","x-ratelimit-remaining-requests","x-ratelimit-limit-tokens","x-ratelimit-remaining-tokens"):
        if resp.headers.get(k) is not None:
            u[k.replace("x-ratelimit-","",1).replace("-","_")]=resp.headers.get(k)
    _save_usage(u)

def is_configured():
    return bool(runtime_cfg.get("groq_key"))

def ask_ai(history):
    key = runtime_cfg.get("groq_key")
    if not key:
        return "I don't have a Groq API key yet - please add it in Settings."
    try:
        started_at=time.perf_counter()
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 700
            },
            timeout=(5,25)
        )
        record_groq_usage(r, started_at)
        if r.status_code in (401, 403):
            return "I couldn't use the Groq API key. Please check your key in Settings."
        if r.status_code in (402, 429):
            return "Groq has reported that the current API quota or rate limit is unavailable."
        if not r.ok:
            return f"I couldn't reach Groq right now (HTTP {r.status_code})."
        text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        return text or "Groq returned an empty response."
    except requests.RequestException as e:
        return f"I ran into a network error talking to Groq: {e}"

def transcribe_groq_audio(audio_bytes, filename="voice.webm", mime_type="audio/webm"):
    key=runtime_cfg.get("groq_key")
    if not key: return "Please add your Groq API key first."
    try:
        started_at=time.perf_counter()
        r=requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization":f"Bearer {key}"},
            files={"file":(filename,audio_bytes,mime_type)},
            data={"model":"whisper-large-v3-turbo","response_format":"json","language":"en"},
            timeout=90
        )
        record_groq_usage(r, started_at)
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
        started_at=time.perf_counter()
        r=requests.post(GROQ_URL,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=payload,timeout=90)
        record_groq_usage(r, started_at)
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
    "google": "https://www.google.com/",
    "reddit": "https://www.reddit.com/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "x": "https://x.com/",
    "twitter": "https://x.com/",
    "tiktok": "https://www.tiktok.com/",
    "wikipedia": "https://www.wikipedia.org/",
    "github": "https://github.com/",
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
    if re.match(r"^[a-z0-9-]+$", target, re.I):
        return "https://www." + target + ".com/"
    return "https://www.google.com/search?q=" + requests.utils.quote(target)

def _needs_memory_lookup(text):
    lower=(text or "").lower()
    cues=(
        r"\bmy\b", r"\bmine\b", r"\bme\b", r"\bi\b",
        r"\bremember\b", r"\bfavourite\b", r"\bfavorite\b",
        r"\bbefore\b", r"\bearlier\b", r"\blast time\b", r"\byesterday\b",
        r"\btomorrow\b", r"\bwhat did i\b", r"\bwhat have i\b",
        r"\bwhat do i have\b", r"\bwhat was i\b", r"\bwho am i\b",
        r"\babout me\b", r"\bwe talked\b", r"\bconversation\b",
        r"\bchat history\b", r"\bprevious\b"
    )
    return any(re.search(pattern,lower) for pattern in cues)

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
        return f'Finding “{q}” on YouTube.', {"type":"play_music","query":q}
    m=re.match(r"(?:open|go to|visit)\s+(.+)",stripped,re.I)
    if m:
        u=normalize_url(m.group(1));return (f"Opening {u}",{"type":"open_url","url":u}) if u else ("Which website would you like me to open?",None)
    m=re.match(r"(?:search for|google|look up|web search)\s+(.+)",stripped,re.I)
    if m:
        q=m.group(1).strip();return web_search(q),{"type":"open_url","url":f"https://www.google.com/search?q={requests.utils.quote(q)}"}
    
    relevant=firebase_relevant_memories(stripped,6) if _needs_memory_lookup(stripped) else []
    h=[conversation_history[0]]
    if relevant:h.append({"role":"system","content":"Relevant long-term Jarvis memory from older conversations:\n\n"+"\n\n---\n\n".join(relevant)})
    h.extend(conversation_history[1:]);h.append({"role":"user","content":stripped});reply=ask_ai(h);conversation_history.append({"role":"user","content":stripped});conversation_history.append({"role":"assistant","content":reply});persist_exchange(stripped,reply);save_chat([m for m in conversation_history[1:] if m.get("role") in ("user","assistant")]);conversation_history[:]=[conversation_history[0]]+conversation_history[-20:];return reply,None

@app.route("/")
def index(): return Response(INDEX_HTML, mimetype="text/html")

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
    allowed={"accent","voiceOn","continuousVoice","voiceURI","voiceMode","expressionMode","reduceLag","chatOpen","camVisible"}
    clean={k:settings[k] for k in allowed if k in settings}
    ok=_firebase_settings_save(clean)
    return jsonify({"ok":ok,"persistent":ok}), (200 if ok else 503)

@app.route("/api/setup",methods=["POST"])
def api_setup():
    data=request.get_json(force=True,silent=True) or {};key=(data.get("groq_key") or "").strip();save=bool(data.get("save"))
    if not key:return jsonify({"ok":False,"error":"A Groq API key is required."}),400
    runtime_cfg["groq_key"]=key
    if save:save_config(runtime_cfg)
    return jsonify({"ok":True})

@app.route("/api/message",methods=["POST"])
def api_message():
    if not is_configured():return jsonify({"error":"Not configured yet."}),400
    data=request.get_json(force=True,silent=True) or {};text=(data.get("text") or "").strip()
    if not text:return jsonify({"reply":"","action":None})
    reply,action=handle_command(text);return jsonify({"reply":reply,"action":action})

@app.route("/api/transcribe",methods=["POST"])
def api_transcribe():
    if not is_configured(): return jsonify({"error":"Please add your Groq API key first."}),400
    audio=request.files.get("audio")
    if not audio: return jsonify({"error":"No microphone audio was received."}),400
    data=audio.read()
    if not data: return jsonify({"error":"The microphone recording was empty."}),400
    text=transcribe_groq_audio(data,audio.filename or "voice.webm",audio.mimetype or "audio/webm")
    if not text or text.startswith(("I couldn't transcribe", "Voice transcription failed")):
        return jsonify({"error":text or "I couldn't hear any words."}),502
    return jsonify({"text":text})

@app.route("/api/photo",methods=["POST"])
def api_photo():
    if not is_configured():return jsonify({"error":"Not configured yet."}),400
    data=request.get_json(force=True,silent=True) or {};data_url=data.get("image") or "";question=(data.get("question") or "").strip()
    if not data_url.startswith("data:image/"):return jsonify({"error":"Invalid image."}),400
    reply=ask_groq_photo(data_url,question)
    return jsonify({"reply":reply})

@app.route("/api/chat")
def api_chat():
    remote=firebase_all_messages() if _firebase_service_account() else []
    return jsonify({"messages":remote or load_chat()})

@app.route("/api/notes",methods=["GET","POST","DELETE"])
def api_notes():
    if request.method=="GET":
        if not os.path.exists(NOTES_FILE): return jsonify({"notes":[]})
        with open(NOTES_FILE,"r",encoding="utf-8") as f: lines=[x.rstrip("\n") for x in f if x.strip()]
        return jsonify({"notes":lines})
    if request.method=="POST":
        data=request.get_json(force=True,silent=True) or {}
        text=(data.get("text") or "").strip()
        if not text:return jsonify({"error":"Note cannot be empty."}),400
        with open(NOTES_FILE,"a",encoding="utf-8") as f:f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {text}\n")
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
    if not os.path.isfile(path):
        return jsonify({"error":"Photo not found."}),404
    try:
        os.remove(path)
    except OSError as e:
        return jsonify({"error":f"Could not delete photo: {e}"}),500
    return jsonify({"ok":True,"persistent":False})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
