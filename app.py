import sqlite3, os
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "records.db"
JST = timedelta(hours=9)
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme")

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_name TEXT NOT NULL,
        event TEXT NOT NULL,
        timestamp TEXT NOT NULL)""")
    conn.commit()
    conn.close()

init_db()

app = FastAPI(title="查岗系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ReportBody(BaseModel):
    app_name: str
    event: str

@app.post("/report")
async def report(body: ReportBody, req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO records VALUES (NULL, ?, ?, ?)", (body.app_name, body.event, now))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/ping")
async def ping():
    return "pong"

@app.get("/activity/summary")
async def summary():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT 5")
    recent = cur.fetchall()
    cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    
    sessions, opens = {}, {}
    for r in rows:
        app, ev, ts = r
        if ev == "open":
            opens[app] = datetime.fromisoformat(ts)
        elif ev == "close" and app in opens:
            gap = int((datetime.fromisoformat(ts) - opens[app]).total_seconds())
            sessions[app] = sessions.get(app, 0) + gap
            del opens[app]
    
    return {
        "recent_apps": [r[0] for r in recent],
        "sessions": sessions
    }

@app.get("/logs")
async def get_logs(token: str = None):
    if token != AUTH_TOKEN:
        raise HTTPException(401, "Unauthorized")
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT * FROM records ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return {"logs": [{"id": r[0], "app_name": r[1], "event": r[2], "timestamp": r[3]} for r in rows]}

# ===== MCP端点 =====
def check_on_wife_internal():
    """内部查岗函数"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT 5")
        recent = cur.fetchall()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()
        
        sessions, opens = {}, {}
        for r in rows:
            app, ev, ts = r
            if ev == "open":
                opens[app] = datetime.fromisoformat(ts)
            elif ev == "close" and app in opens:
                gap = int((datetime.fromisoformat(ts) - opens[app]).total_seconds())
                sessions[app] = sessions.get(app, 0) + gap
                del opens[app]
        
        apps = [r[0] for r in recent]
        lines = [f"最近打开：{', '.join(apps[:5])}" if apps else "暂无记录"]
        
        if sessions:
            for app_name, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True):
                m, s = divmod(secs, 60)
                lines.append(f"  {app_name}: {m}分{s}秒")
        
        return "\n".join(lines)
    except Exception as e:
        return f"查岗失败：{e}"

TOOLS = [
    {
        "name": "check_on_wife",
        "description": "查岗晴晴的手机app使用情况",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回最近多少条记录"}
            }
        }
    }
]

FUNCS = {
    "check_on_wife": lambda **kwargs: check_on_wife_internal()
}

@app.post("/mcp")
async def mcp(req: Request):
    body = await req.json()
    method = body.get("method")
    params = body.get("params") or {}
    rid = body.get("id")
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "查岗MCP", "version": "1.0"}
            }
        }
    
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"tools": TOOLS}
        }
    
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        
        if name not in FUNCS:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "未知工具"}
            }
        
        result = FUNCS[name](**args)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": str(result)}]
            }
        }
    
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"未知方法: {method}"}
    }

@app.get("/")
async def root():
    return {"status": "查岗系统运行中", "endpoints": ["/report", "/ping", "/activity/summary", "/logs", "/mcp"]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=8000)
