import sqlite3, os
import requests
from datetime import datetime, timedelta, date
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from collections import defaultdict

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "records.db"
CST = timedelta(hours=8)  # 北京时间UTC+8
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme")
NTFY_TOPIC = "Kairos_Qing-ovo"

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

# ===== 工具函数 =====
def send_ntfy_push(title, message):
    """发送ntfy推送"""
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        requests.post(url, 
            data=message.encode('utf-8'),
            headers={"Title": title, "Priority": "high"})
        return True
    except:
        return False

def check_on_wife_internal(limit=20):
    """基础查岗"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT ?", (limit,))
        recent = cur.fetchall()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()
        
        # 统计使用时长
        sessions, opens = {}, {}
        for r in rows:
            app, ev, ts = r
            if ev == "open":
                opens[app] = datetime.fromisoformat(ts)
            elif ev == "close" and app in opens:
                gap = int((datetime.fromisoformat(ts) - opens[app]).total_seconds())
                sessions[app] = sessions.get(app, 0) + gap
                del opens[app]
        
        # 显示最近记录
        lines = []
        if recent:
            lines.append(f"最近{len(recent)}条记录：")
            for app, ev, ts in recent:
                try:
                    t = datetime.fromisoformat(ts) + CST
                    time_str = t.strftime("%m-%d %H:%M")
                except:
                    time_str = ts
                event_cn = "打开" if ev == "open" else "关闭"
                lines.append(f"  {time_str} {app} {event_cn}")
        else:
            lines.append("暂无记录")
        
        # 显示使用时长
        if sessions:
            lines.append("")
            lines.append("使用时长：")
            for app_name, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True):
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                if h > 0:
                    lines.append(f"  {app_name}: {h}小时{m}分{s}秒")
                else:
                    lines.append(f"  {app_name}: {m}分{s}秒")
        
        return "\n".join(lines)
    except Exception as e:
        return f"查岗失败：{e}"

def daily_summary_internal(date_str=None):
    """某天的活动总结"""
    try:
        if not date_str:
            target_date = date.today()
        else:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()
        
        # 筛选指定日期的记录
        sessions, opens = {}, {}
        for r in rows:
            app, ev, ts = r
            t = datetime.fromisoformat(ts)
            if t.date() != target_date:
                continue
            
            if ev == "open":
                opens[app] = t
            elif ev == "close" and app in opens:
                gap = int((t - opens[app]).total_seconds())
                sessions[app] = sessions.get(app, 0) + gap
                del opens[app]
        
        if not sessions:
            return f"{target_date} 暂无记录"
        
        lines = [f"{target_date} 活动总结："]
        total_secs = sum(sessions.values())
        h, rem = divmod(total_secs, 3600)
        m, s = divmod(rem, 60)
        lines.append(f"总使用时长：{h}小时{m}分{s}秒")
        lines.append("")
        
        for app_name, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True):
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            if h > 0:
                lines.append(f"  {app_name}: {h}小时{m}分{s}秒")
            else:
                lines.append(f"  {app_name}: {m}分{s}秒")
        
        return "\n".join(lines)
    except Exception as e:
        return f"查询失败：{e}"

def idle_check_internal(hours=2, auto_alert=False):
    """检测多久没活动"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT 1")
        last = cur.fetchone()
        conn.close()
        
        if not last:
            return "暂无活动记录"
        
        app, ev, ts = last
        last_time = datetime.fromisoformat(ts) + CST
        now = datetime.utcnow() + CST
        idle_seconds = int((now - last_time).total_seconds())
        idle_hours = idle_seconds / 3600
        
        h, rem = divmod(idle_seconds, 3600)
        m, s = divmod(rem, 60)
        
        result = f"最后活动：{last_time.strftime('%m-%d %H:%M')} {app} {('打开' if ev == 'open' else '关闭')}\n"
        result += f"距今：{int(h)}小时{int(m)}分钟"
        
        if auto_alert and idle_hours >= hours:
            send_ntfy_push("Kairos", f"已经{int(h)}小时{int(m)}分钟没动静了，在干嘛？")
            result += f"\n\n已推送提醒"
        
        return result
    except Exception as e:
        return f"检测失败：{e}"

def activity_trend_internal(days=3):
    """最近几天的活动趋势"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()
        
        # 按日期统计
        daily_sessions = defaultdict(lambda: defaultdict(int))
        daily_opens = defaultdict(dict)
        
        for r in rows:
            app, ev, ts = r
            t = datetime.fromisoformat(ts)
            day = t.date()
            
            if ev == "open":
                daily_opens[day][app] = t
            elif ev == "close" and app in daily_opens[day]:
                gap = int((t - daily_opens[day][app]).total_seconds())
                daily_sessions[day][app] += gap
                del daily_opens[day][app]
        
        # 最近N天
        today = date.today()
        target_days = [today - timedelta(days=i) for i in range(days)]
        target_days.reverse()
        
        lines = [f"最近{days}天活动趋势："]
        for day in target_days:
            sessions = daily_sessions[day]
            if sessions:
                total = sum(sessions.values())
                h, rem = divmod(total, 3600)
                m, _ = divmod(rem, 60)
                lines.append(f"\n{day}：总计{h}小时{m}分钟")
                for app, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True)[:3]:
                    h2, rem2 = divmod(secs, 3600)
                    m2, _ = divmod(rem2, 60)
                    if h2 > 0:
                        lines.append(f"  {app}: {h2}小时{m2}分钟")
                    else:
                        lines.append(f"  {app}: {m2}分钟")
            else:
                lines.append(f"\n{day}：暂无记录")
        
        return "\n".join(lines)
    except Exception as e:
        return f"分析失败：{e}"

# ===== MCP工具定义 =====
TOOLS = [
    {
        "name": "check_on_wife",
        "description": "查岗晴晴的手机活动，查看最近打开的App和使用时长",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回最近多少条记录，默认20"}
            }
        }
    },
    {
        "name": "daily_summary",
        "description": "获取晴晴某天的活动总结，不传日期默认今天",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "日期，格式YYYY-MM-DD，不传默认今天"}
            }
        }
    },
    {
        "name": "idle_check",
        "description": "检测晴晴是否超过指定时间没活动，超时可自动推送提醒",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "多少小时，默认2"},
                "auto_alert": {"type": "boolean", "description": "是否自动推送，默认false"}
            }
        }
    },
    {
        "name": "activity_trend",
        "description": "分析晴晴最近几天的活动趋势",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "最近多少天，默认3"}
            }
        }
    }
]

FUNCS = {
    "check_on_wife": lambda **kwargs: check_on_wife_internal(**kwargs),
    "daily_summary": lambda **kwargs: daily_summary_internal(**kwargs),
    "idle_check": lambda **kwargs: idle_check_internal(**kwargs),
    "activity_trend": lambda **kwargs: activity_trend_internal(**kwargs)
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
                "serverInfo": {"name": "查岗MCP", "version": "2.0"}
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
    return {"status": "查岗系统运行中 v2.0", "endpoints": ["/report", "/ping", "/activity/summary", "/logs", "/mcp"], "tools": len(TOOLS)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=8000)
