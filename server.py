import os
import json
import sqlite3
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware

DB = Path(os.environ.get("WATCH_DB") or Path(__file__).with_name("watch.db"))
DB.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Avenir WatchSync")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS items(
                url TEXT PRIMARY KEY, title TEXT, time REAL, duration REAL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
        )
        cols = [r[1] for r in c.execute("PRAGMA table_info(items)").fetchall()]
        for col, typ in (("fav", "INTEGER DEFAULT 0"), ("poster", "TEXT"), ("device", "TEXT"), ("episode", "TEXT"), ("ep_label", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")


init()

_TRACK_KEYS = {"fbclid", "gclid", "yclid", "msclkid", "igshid", "_openstat", "ysclid"}


def norm(u: str) -> str:
    try:
        s = urlsplit(u)
        q = [
            (k, v)
            for k, v in parse_qsl(s.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACK_KEYS
        ]
        return urlunsplit((s.scheme, s.netloc, s.path.rstrip("/"), urlencode(q), ""))
    except Exception:
        return u


@app.post("/api/progress")
async def progress(req: Request):
    d = await req.json()
    url = norm(d.get("url", ""))
    dur = float(d.get("duration") or 0)
    t = float(d.get("time") or 0)
    if not url or t < 5 or (dur and dur < 60):
        return {"ok": False}
    with db() as c:
        c.execute(
            """INSERT INTO items(url, title, time, duration, device, episode, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(url) DO UPDATE SET
                 title=excluded.title, time=excluded.time,
                 duration=excluded.duration, device=excluded.device,
                 episode=excluded.episode, updated_at=datetime('now')""",
            (url, d.get("title", ""), t, dur, d.get("device", ""), d.get("episode", "")),
        )
    return {"ok": True}


@app.post("/api/meta")
async def meta(req: Request):
    d = await req.json()
    url = norm(d.get("url", ""))
    if not url:
        return {"ok": False}
    poster = d.get("poster", "")
    ep = d.get("ep_label", "")
    with db() as c:
        if poster:
            c.execute(
                "UPDATE items SET poster=? WHERE url=? AND (poster IS NULL OR poster='')",
                (poster, url),
            )
        if ep:
            c.execute("UPDATE items SET ep_label=? WHERE url=?", (ep, url))
    return {"ok": True}


@app.get("/api/item")
def item(url: str):
    with db() as c:
        r = c.execute("SELECT time, duration, episode FROM items WHERE url=?", (norm(url),)).fetchone()
    return dict(r) if r else {}


@app.get("/api/list")
def listing():
    with db() as c:
        rows = c.execute(
            "SELECT *, CAST((julianday('now') - julianday(updated_at)) * 86400 AS INTEGER) AS ago "
            "FROM items ORDER BY fav DESC, updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/fav")
def set_fav(url: str, fav: int = 1):
    with db() as c:
        c.execute("UPDATE items SET fav=? WHERE url=?", (1 if fav else 0, norm(url)))
    return {"ok": True}


@app.delete("/api/item")
def delete(url: str):
    with db() as c:
        c.execute("DELETE FROM items WHERE url=?", (norm(url),))
    return {"ok": True}


@app.delete("/api/all")
def delete_all():
    with db() as c:
        c.execute("DELETE FROM items")
    return {"ok": True}


# ── Telegram ──────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", "")
_tg_chat = os.environ.get("TG_CHAT", "")


def _tg_api(method, params):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}?" + urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def tg_chat_id():
    global _tg_chat
    if _tg_chat:
        return _tg_chat
    try:
        d = _tg_api("getUpdates", {})
        for u in reversed(d.get("result", [])):
            m = u.get("message") or u.get("edited_message") or {}
            cid = (m.get("chat") or {}).get("id")
            if cid:
                _tg_chat = str(cid)
                return _tg_chat
    except Exception:
        pass
    return ""


@app.post("/api/tg")
def tg_send(url: str):
    if not TG_TOKEN:
        return {"ok": False, "err": "бот не настроен"}
    chat = tg_chat_id()
    if not chat:
        return {"ok": False, "err": "напиши боту сообщение и повтори"}
    with db() as c:
        r = c.execute(
            "SELECT title, time, poster, ep_label FROM items WHERE url=?", (norm(url),)
        ).fetchone()
    if not r:
        return {"ok": False, "err": "не найдено"}
    title = r["title"] or url
    t = int(r["time"] or 0)
    stamp = (f"{t//3600}ч {(t%3600)//60} мин" if t >= 3600 else f"{t//60} мин")
    caption = f"🎬 {title}\n"
    if r["ep_label"]:
        caption += f"📺 {r['ep_label']} серия\n"
    caption += f"⏱ Продолжить с {stamp}\n{norm(url)}"
    try:
        if r["poster"]:
            _tg_api("sendPhoto", {"chat_id": chat, "photo": r["poster"], "caption": caption})
        else:
            _tg_api("sendMessage", {"chat_id": chat, "text": caption})
        return {"ok": True}
    except Exception:
        try:  # постер не принялся — шлём текстом
            _tg_api("sendMessage", {"chat_id": chat, "text": caption})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}


ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="112" fill="#0b0b0d"/>'
    '<g stroke="#f4f2ea" stroke-width="18" stroke-linecap="round" stroke-linejoin="round" fill="none">'
    '<path d="M256 134V196"/><path d="M256 316v62"/>'
    '<path d="M134 256h62"/><path d="M316 256h62"/>'
    '<rect x="240" y="80" width="32" height="32" rx="4" transform="rotate(45 256 96)"/>'
    '<rect x="240" y="400" width="32" height="32" rx="4" transform="rotate(45 256 416)"/>'
    '<rect x="80" y="240" width="32" height="32" rx="4" transform="rotate(45 96 256)"/>'
    '<rect x="400" y="240" width="32" height="32" rx="4" transform="rotate(45 416 256)"/>'
    "</g>"
    '<path fill="#f4f2ea" d="M256 176Q280 232 336 256Q280 280 256 336'
    'Q232 280 176 256Q232 232 256 176Z"/>'
    "</svg>"
)


@app.get("/icon.svg")
def icon():
    return Response(ICON_SVG, media_type="image/svg+xml")


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(
        {
            "name": "Avenir WatchSync",
            "short_name": "WatchSync",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b0b0d",
            "theme_color": "#0b0b0d",
            "icons": [
                {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}
            ],
        }
    )


@app.get("/kiwi.zip")
def kiwi():
    return FileResponse(
        Path(__file__).with_name("watch-sync-kiwi.zip"),
        media_type="application/zip",
        filename="watch-sync-kiwi.zip",
    )


@app.get("/", response_class=HTMLResponse)
def home():
    return DASH


DASH = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Avenir WatchSync</title>
<meta name="theme-color" content="#0b0b0d">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<style>
  :root { color-scheme: dark; --bg:#0b0b0d; --card:#15161c; --line:#242631; --mut:#9498a4; --acc:#5b8cff; }
  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body { margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:#e8e8ea; }
  svg { display:block; }
  header { position:sticky; top:0; z-index:5; background:rgba(11,11,13,.9); backdrop-filter:blur(8px);
           border-bottom:1px solid var(--line); }
  .hwrap { max-width:1240px; margin:0 auto; padding:14px 20px; }
  .brand { display:flex; align-items:center; gap:10px; }
  .brand img { width:30px; height:30px; border-radius:8px; }
  .brand b { font-size:19px; font-weight:800; letter-spacing:.2px; }
  .controls { display:flex; gap:10px; margin-top:12px; }
  .qwrap { position:relative; flex:1; min-width:0; }
  .qic { position:absolute; left:14px; top:50%; transform:translateY(-50%); color:var(--mut); pointer-events:none; }
  #q { width:100%; padding:11px 14px 11px 42px; border-radius:11px; border:1px solid var(--line);
       background:var(--card); color:#e8e8ea; font-size:15px; }
  .filter-btn { display:inline-flex; align-items:center; gap:8px; padding:0 16px; border-radius:11px;
                border:1px solid var(--line); background:var(--card); color:#e8e8ea; font-size:14px; cursor:pointer;
                white-space:nowrap; }
  .filter-btn:hover { border-color:#39405a; }
  .filter-panel { position:fixed; z-index:75; background:#1b1d26; border:1px solid #2c2f3d; border-radius:12px;
                  padding:7px; width:214px; box-shadow:0 14px 34px rgba(0,0,0,.55); display:none; }
  .filter-panel.show { display:block; }
  .fp-label { color:var(--mut); font-size:10.5px; text-transform:uppercase; letter-spacing:.6px; padding:9px 11px 4px; }
  .filter-panel button { display:flex; align-items:center; justify-content:space-between; width:100%; text-align:left;
                         background:none; border:0; color:#e8e8ea; font-size:14px; padding:9px 11px; border-radius:8px; cursor:pointer; }
  .filter-panel button:hover { background:#262a36; }
  .filter-panel button.on { color:var(--acc); font-weight:600; }
  .filter-panel button.on::after { content:'✓'; }

  .wrap { max-width:1240px; margin:0 auto; padding:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(210px, 1fr)); gap:16px; }

  .card { position:relative; border-radius:14px; overflow:hidden; background:#0e0f13; border:1px solid var(--line);
          transition:transform .15s, border-color .15s; }
  .card:hover { transform:translateY(-3px); border-color:#39405a; }
  .card.fav { border-color:var(--line); }
  .pw { position:relative; display:block; aspect-ratio:2/3; }
  .ph { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#3a4150; }
  .poster { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; transition:transform .3s; }
  .card:hover .poster { transform:scale(1.05); }
  .overlay { position:absolute; left:0; right:0; bottom:0; padding:34px 12px 14px;
             background:linear-gradient(to top, rgba(0,0,0,.95), rgba(0,0,0,.5) 60%, transparent); }
  .title { font-size:13.5px; font-weight:700; line-height:1.3; color:#fff; text-shadow:0 1px 4px rgba(0,0,0,.9);
           display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .orow { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:10px; }
  .play { display:inline-flex; align-items:center; gap:6px; background:rgba(0,0,0,.6); color:#fff;
          font-weight:700; font-size:12.5px; padding:6px 12px; border-radius:20px; white-space:nowrap;
          backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,.16); }
  .cmeta { color:#c7cad3; font-size:10.5px; text-shadow:0 1px 3px rgba(0,0,0,.9); overflow:hidden;
           white-space:nowrap; text-overflow:ellipsis; display:flex; align-items:center; gap:5px; }
  .dev { display:inline-flex; align-items:center; gap:4px; }
  .blive { position:absolute; left:8px; top:8px; z-index:2; display:inline-flex; align-items:center; gap:5px;
           background:rgba(0,0,0,.7); color:#fff; font-weight:600; font-size:11px; padding:4px 9px; border-radius:20px; }
  .blive .dot { width:7px; height:7px; border-radius:50%; background:#ff5c5c; animation:pulse 1.3s infinite; }
  @keyframes pulse { 50% { opacity:.25; } }
  .menu-btn { position:absolute; right:6px; top:6px; z-index:3; background:rgba(0,0,0,.35); border:0; color:#fff;
              cursor:pointer; width:34px; height:34px; border-radius:10px; display:flex; align-items:center; justify-content:center; }
  .menu-btn:hover { background:rgba(0,0,0,.6); }
  .pbar { position:absolute; left:0; right:0; bottom:0; height:4px; z-index:2; background:rgba(255,255,255,.28); }
  .pbar > i { display:block; height:100%; background:var(--acc); }

  .cardmenu { position:fixed; z-index:70; background:rgba(23,25,32,.97); backdrop-filter:blur(14px);
              border:1px solid #303443; border-radius:14px; padding:6px; width:240px;
              box-shadow:0 18px 46px rgba(0,0,0,.6); display:none; }
  .cardmenu.show { display:block; }
  .cardmenu button { display:flex; align-items:center; gap:2px; width:100%; text-align:left; background:none;
                     border:0; color:#eceef3; font-size:14.5px; padding:11px 11px; border-radius:10px; cursor:pointer;
                     transition:background .12s; }
  .cardmenu button .mi { width:30px; display:inline-flex; align-items:center; color:#9aa0ad; flex-shrink:0; transition:color .12s; }
  .cardmenu button:hover { background:#2a2e3a; }
  .cardmenu button:hover .mi { color:#eceef3; }
  .cardmenu .sep { height:1px; background:#2a2e3a; margin:5px 10px; }
  .cardmenu button.dngr, .cardmenu button.dngr .mi { color:#ff6b6b; }
  .cardmenu button.dngr:hover { background:rgba(229,72,77,.15); }
  .cardmenu svg, .menu-btn svg, .cmeta svg, .play svg, .qic svg, .filter-btn svg { color:currentColor; }

  .sect-head { margin:26px 0 12px; color:var(--mut); font-size:14px; cursor:pointer; user-select:none;
               border-top:1px solid var(--line); padding-top:18px; }
  .empty { text-align:center; padding:70px 20px; color:var(--mut); }
  .empty img { width:66px; height:66px; opacity:.55; margin-bottom:14px; border-radius:14px; }

  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.7); display:none; align-items:center;
                   justify-content:center; z-index:80; padding:20px; }
  .modal-overlay.show { display:flex; }
  .modal { background:var(--card); border:1px solid #2c2f3d; border-radius:16px; padding:22px; max-width:360px; width:100%; }
  .modal-title { font-size:17px; font-weight:700; margin-bottom:8px; word-break:break-word; }
  .modal-text { color:var(--mut); font-size:13px; margin-bottom:18px; }
  .modal-row { display:flex; gap:10px; }
  .mbtn { flex:1; padding:12px; border:0; border-radius:10px; font-size:15px; font-weight:600; cursor:pointer; }
  .mbtn.cancel { background:#22242f; color:#e8e8ea; }
  .mbtn.danger { background:#e5484d; color:#fff; }
  .toast { position:fixed; left:50%; bottom:24px; transform:translateX(-50%) translateY(20px); background:#22242f;
           color:#e8e8ea; padding:12px 18px; border-radius:12px; font-size:14px; opacity:0; pointer-events:none;
           transition:.25s; z-index:90; box-shadow:0 8px 26px rgba(0,0,0,.5); }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }

  @keyframes cardIn { from { opacity:0; transform:translateY(14px) scale(.985); } to { opacity:1; transform:none; } }
  .grid.anim > .card { animation: cardIn .42s cubic-bezier(.2,.7,.3,1) both; animation-delay: calc(var(--i,0) * .04s); }
  .card.fav { box-shadow:0 6px 30px rgba(202,162,60,.32); }
  .fav-badge { position:absolute; left:8px; top:8px; z-index:2; width:27px; height:27px; border-radius:8px;
               background:rgba(0,0,0,.5); color:#f0c14b; display:flex; align-items:center; justify-content:center;
               box-shadow:0 2px 8px rgba(0,0,0,.45); }
  .now { color:#ff6b6b; font-weight:700; }
  .epline { display:inline-block; margin-top:5px; color:#071026; background:#a9c4ff; font-weight:700;
            font-size:11px; padding:2px 8px; border-radius:20px; }
  #watched { max-height:0; opacity:0; overflow:hidden; transition:max-height .45s ease, opacity .35s ease; }
  #watched.open { max-height:6000px; opacity:1; }
  .sect-head { transition:color .15s; } .sect-head:hover { color:#e8e8ea; }
  .cardmenu.show, .filter-panel.show { animation:pop .16s ease both; transform-origin:top right; }
  @keyframes pop { from { opacity:0; transform:scale(.95) translateY(-5px); } to { opacity:1; transform:none; } }
  .modal-overlay.show .modal { animation:pop .18s ease both; transform-origin:center; }
  @media (max-width:560px) {
    .grid { grid-template-columns:1fr; gap:14px; }
    .wrap { padding:14px; }
    .title { font-size:17px; }
    .play { font-size:14px; padding:8px 15px; }
    .cmeta { font-size:12px; }
    .overlay { padding:46px 16px 18px; }
    .menu-btn { width:42px; height:42px; }
  }
</style>
</head>
<body>
<header><div class="hwrap">
  <div class="brand"><img src="/icon.svg" alt=""><b>Avenir WatchSync</b></div>
  <div class="controls">
    <div class="qwrap">
      <span class="qic" id="qic"></span>
      <input id="q" type="search" placeholder="Поиск...">
    </div>
    <button class="filter-btn" id="filterBtn" onclick="toggleFilter(event)">Фильтр</button>
  </div>
</div></header>

<div class="filter-panel" id="filterPanel">
  <div class="fp-label">Показать</div>
  <button data-f="all" class="on">Все</button>
  <button data-f="prog">В процессе</button>
  <button data-f="fav">Избранное</button>
  <div class="fp-label">Сортировка</div>
  <button data-s="recent" class="on">Недавние</button>
  <button data-s="progress">По прогрессу</button>
  <button data-s="title">По названию</button>
</div>

<div class="wrap">
  <div class="grid" id="list"></div>
  <div id="watched-wrap" style="display:none">
    <div class="sect-head" id="watched-head" onclick="toggleWatched()">▸ Просмотрено (<span id="wcount">0</span>)</div>
    <div class="grid" id="watched"></div>
  </div>
</div>

<div class="cardmenu" id="cardmenu"></div>

<div class="modal-overlay" id="modal"><div class="modal">
  <div class="modal-title" id="modal-title">Удалить?</div>
  <div class="modal-text">Это действие необратимо.</div>
  <div class="modal-row">
    <button class="mbtn cancel" onclick="modalNo()">Отмена</button>
    <button class="mbtn danger" onclick="modalYes()">Удалить</button>
  </div>
</div></div>
<div class="toast" id="toast"></div>

<script>
// ── монохромный набор иконок (currentColor, один стиль) ──
const IC = {
  play:  '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5v14l12-7z"/></svg>',
  dots:  '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="12" cy="19" r="1.9"/></svg>',
  star:  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.7 5.5 6 .9-4.35 4.25 1.03 6L12 17.3 6.62 20.1l1.03-6L3.3 9.9l6-.9z"/></svg>',
  starF: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3.5l2.7 5.5 6 .9-4.35 4.25 1.03 6L12 17.3 6.62 20.1l1.03-6L3.3 9.9l6-.9z"/></svg>',
  send:  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg>',
  trash: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  laptop:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 20h20"/></svg>',
  phone: '<svg width="12" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/></svg>',
  search:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
  film:  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4"/></svg>',
};
document.getElementById('qic').innerHTML = IC.search;
document.getElementById('filterBtn').insertAdjacentHTML('afterbegin',
  '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M3 5h18M6 12h12M10 19h4"/></svg>');

let ITEMS = [], watchedOpen = false, toastT, pendingDel = null, menuUrl = null;
let filterMode = 'all', sortKey = 'recent';
const q = document.getElementById('q');
q.addEventListener('input', render);

function rtime(s){ s=Math.floor(s||0); const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h?`${h}ч ${m} мин`:`${m} мин`; }
function ago(sec){ sec=Math.max(0,sec|0); if(sec<45)return'только что'; if(sec<3600)return Math.round(sec/60)+' мин назад'; if(sec<86400)return Math.round(sec/3600)+' ч назад'; return Math.round(sec/86400)+' дн назад'; }
const pctOf = it => it.duration ? Math.min(100,100*it.time/it.duration) : 0;
const isWatched = it => it.duration && pctOf(it) >= 90;
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
function devHTML(dev){
  if(!dev) return '';
  const phone = /телефон|📱/i.test(dev);
  return `<span class="dev" title="${phone?'Телефон':'ПК'}">${phone?IC.phone:IC.laptop}</span>`;
}

function showToast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(()=>el.classList.remove('show'),2600); }
async function tg(url){ try{ const r=await(await fetch('/api/tg?url='+encodeURIComponent(url),{method:'POST'})).json(); showToast(r.ok?'Отправлено в Telegram':(r.err||'ошибка')); }catch(e){ showToast('сервер недоступен'); } }
async function toggleFav(url, fav){ await fetch('/api/fav?fav='+fav+'&url='+encodeURIComponent(url),{method:'POST'}); load(); }
function toggleWatched(){ watchedOpen=!watchedOpen; document.getElementById('watched').classList.toggle('open', watchedOpen); document.getElementById('watched-head').firstChild.textContent=(watchedOpen?'▾':'▸')+' Просмотрено ('; }

// ── фильтр ──
function toggleFilter(e){
  e.stopPropagation();
  const p=document.getElementById('filterPanel');
  if(p.classList.contains('show')){ p.classList.remove('show'); return; }
  const r=e.currentTarget.getBoundingClientRect();
  p.style.top=(r.bottom+6)+'px'; p.style.right=Math.max(8, window.innerWidth-r.right)+'px';
  p.classList.add('show');
}
document.getElementById('filterPanel').addEventListener('click', e=>{
  e.stopPropagation();
  const b=e.target.closest('button'); if(!b) return;
  if(b.dataset.f!==undefined){ filterMode=b.dataset.f; document.querySelectorAll('#filterPanel [data-f]').forEach(x=>x.classList.toggle('on', x===b)); }
  if(b.dataset.s!==undefined){ sortKey=b.dataset.s; document.querySelectorAll('#filterPanel [data-s]').forEach(x=>x.classList.toggle('on', x===b)); }
  render();
});

// ── ⋮ меню (тоггл) ──
function openMenu(e, url){
  e.preventDefault(); e.stopPropagation();
  const m=document.getElementById('cardmenu');
  if(m.classList.contains('show') && menuUrl===url){ closeMenu(); return; } // повторный тап — закрыть
  const it=ITEMS.find(x=>x.url===url); if(!it) return;
  menuUrl=url;
  m.innerHTML =
    `<button onclick="mFav()"><span class="mi">${it.fav?IC.starF:IC.star}</span>${it.fav?'Убрать из избранного':'В избранное'}</button>`+
    `<button onclick="mTg()"><span class="mi">${IC.send}</span>Отправить в Telegram</button>`+
    `<div class="sep"></div>`+
    `<button class="dngr" onclick="mDel()"><span class="mi">${IC.trash}</span>Удалить</button>`;
  m.classList.add('show');
  const r=e.currentTarget.getBoundingClientRect(), mw=230;
  m.style.left=Math.max(8, Math.min(r.right-mw, window.innerWidth-mw-8))+'px';
  m.style.top=Math.min(r.bottom+6, window.innerHeight-170)+'px';
}
function closeMenu(){ document.getElementById('cardmenu').classList.remove('show'); menuUrl=null; }
function mFav(){ const it=ITEMS.find(x=>x.url===menuUrl); if(it) toggleFav(menuUrl, it.fav?0:1); closeMenu(); }
function mTg(){ const u=menuUrl; closeMenu(); tg(u); }
function mDel(){ const u=menuUrl; closeMenu(); del(u); }
document.addEventListener('click', ()=>{ closeMenu(); document.getElementById('filterPanel').classList.remove('show'); });

// ── удаление ──
function del(url){ pendingDel=url; const it=ITEMS.find(x=>x.url===url); document.getElementById('modal-title').textContent='Удалить «'+((it&&it.title)||url).slice(0,50)+'»?'; document.getElementById('modal').classList.add('show'); }
function modalNo(){ pendingDel=null; document.getElementById('modal').classList.remove('show'); }
async function modalYes(){ const u=pendingDel; modalNo(); if(!u)return; await fetch('/api/item?url='+encodeURIComponent(u),{method:'DELETE'}); load(); }
document.getElementById('modal').addEventListener('click',e=>{ if(e.target.id==='modal')modalNo(); });

function cardHTML(it, i){
  const pct=pctOf(it), hasDur=it.duration>0, live=it.ago!=null&&it.ago<15;
  const u=JSON.stringify(it.url), href=esc(it.url), name=esc((it.title||it.url).slice(0,140));
  const poster = it.poster ? `<img class="poster" src="${esc(it.poster)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.remove()">` : '';
  const cmeta = live ? '<span class="now">● сейчас</span>' : devHTML(it.device) + (it.ago!=null ? `<span>${ago(it.ago)}</span>` : '');
  return `<div class="card ${it.fav?'fav':''}" style="--i:${i}">
    <a class="pw" href="${href}" target="_blank" rel="noopener">
      <span class="ph">${IC.film}</span>${poster}
      ${it.fav?`<span class="fav-badge">${IC.starF}</span>`:''}
      <div class="overlay">
        <div class="title">${name}</div>
        ${it.ep_label?`<div class="epline">${esc(it.ep_label)} серия</div>`:''}
        <div class="orow">
          <span class="play">${IC.play} ${rtime(it.time)}</span>
          <span class="cmeta">${cmeta}</span>
        </div>
      </div>
      ${hasDur?`<div class="pbar"><i style="width:${pct}%"></i></div>`:''}
    </a>
    <button class="menu-btn" title="Ещё" onclick='openMenu(event, ${u})'>${IC.dots}</button>
  </div>`;
}

let firstRender=true, lastSig='';
function sig(a){ return a.map(it=>it.url+'|'+it.fav+'|'+(it.poster?1:0)+'|'+Math.round((it.time||0)/15)+'|'+(it.ago!=null&&it.ago<15?'L':'')+'|'+(it.title||'')).join(';'); }
function render(){
  const term=q.value.trim().toLowerCase();
  let items=ITEMS.filter(it=>(it.title||it.url).toLowerCase().includes(term));
  if(filterMode==='fav') items=items.filter(it=>it.fav);
  if(filterMode==='prog') items=items.filter(it=>!isWatched(it));
  items.sort((a,b)=>{ if((b.fav?1:0)!==(a.fav?1:0))return(b.fav?1:0)-(a.fav?1:0); if(sortKey==='progress')return pctOf(b)-pctOf(a); if(sortKey==='title')return(a.title||'').localeCompare(b.title||''); return 0; });
  const searching = term.length > 0;
  const cont = searching ? items : items.filter(it=>!isWatched(it));
  const watched = searching ? [] : items.filter(isWatched);
  const listEl=document.getElementById('list'), wg=document.getElementById('watched');
  if(!cont.length){ listEl.innerHTML='<div class="empty" style="grid-column:1/-1"><img src="/icon.svg">'+(term?'<div>Ничего не найдено</div>':'<div>Пока пусто.<br>Начни смотреть что-нибудь — карточка появится тут.</div>')+'</div>'; }
  else { listEl.innerHTML=cont.map((it,i)=>cardHTML(it,i)).join(''); }
  const ww=document.getElementById('watched-wrap');
  if(watched.length){ ww.style.display='block'; document.getElementById('wcount').textContent=watched.length; wg.innerHTML=watched.map((it,i)=>cardHTML(it,i)).join(''); }
  else { ww.style.display='none'; }
  if(firstRender){ firstRender=false; listEl.classList.add('anim'); wg.classList.add('anim'); setTimeout(()=>{listEl.classList.remove('anim'); wg.classList.remove('anim');},1000); }
}
async function load(){ try{ const d=await(await fetch('/api/list')).json(); const s=sig(d); if(s===lastSig)return; lastSig=s; ITEMS=d; render(); }catch(e){} }
load();
setInterval(load, 5000);
</script>
</body>
</html>"""
