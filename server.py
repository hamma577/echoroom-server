"""
ECHO ROOM — Backend API  (complete)
=====================================
Run:
  pip install fastapi uvicorn requests python-jose
  python server.py
"""

import os, json, uuid, datetime, random, re, sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as req
import uvicorn

# ══════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════
DB_PATH = os.environ.get("DB_PATH", "echoroom.db")

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id           TEXT PRIMARY KEY,
        username     TEXT NOT NULL,
        email        TEXT NOT NULL UNIQUE,
        password     TEXT NOT NULL,
        avatar       TEXT DEFAULT '??',
        avatar_color TEXT DEFAULT '#8b5cf6',
        bio          TEXT DEFAULT '',
        plus         INTEGER DEFAULT 0,
        theme_color  TEXT DEFAULT 'purple',
        echo_coins   INTEGER DEFAULT 0,
        joined       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token   TEXT PRIMARY KEY,
        user_id TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS posts (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        username   TEXT NOT NULL,
        avatar     TEXT DEFAULT '?',
        text       TEXT NOT NULL,
        topic      TEXT DEFAULT 'society',
        stance     TEXT DEFAULT 'neutral',
        likes      INTEGER DEFAULT 0,
        is_seed    INTEGER DEFAULT 0,
        timestamp  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS engagements (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        post_id      TEXT NOT NULL,
        response     TEXT NOT NULL,
        score_json   TEXT DEFAULT '{}',
        total_score  INTEGER DEFAULT 0,
        mind_changed INTEGER DEFAULT 0,
        coins_earned INTEGER DEFAULT 5,
        timestamp    TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS scores (
        user_id      TEXT PRIMARY KEY,
        total        INTEGER DEFAULT 0,
        engagements  INTEGER DEFAULT 0,
        mind_changes INTEGER DEFAULT 0,
        quality_avg  REAL DEFAULT 0,
        badges       TEXT DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS rooms (
        id            TEXT PRIMARY KEY,
        question      TEXT NOT NULL,
        category      TEXT DEFAULT 'society',
        room_code     TEXT UNIQUE,
        is_private    INTEGER DEFAULT 0,
        created_by    TEXT DEFAULT '',
        message_count INTEGER DEFAULT 0,
        created       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS room_messages (
        id        TEXT PRIMARY KEY,
        room_id   TEXT NOT NULL,
        user_id   TEXT NOT NULL,
        username  TEXT NOT NULL,
        avatar    TEXT DEFAULT '?',
        text      TEXT NOT NULL,
        timestamp TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reels (
        id        TEXT PRIMARY KEY,
        user_id   TEXT NOT NULL,
        username  TEXT NOT NULL,
        avatar    TEXT DEFAULT '?',
        video_url TEXT NOT NULL,
        caption   TEXT DEFAULT '',
        topic     TEXT DEFAULT 'society',
        likes     INTEGER DEFAULT 0,
        views     INTEGER DEFAULT 0,
        created   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reel_likes (
        reel_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (reel_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS market_items (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        emoji       TEXT DEFAULT '?',
        price       INTEGER DEFAULT 100,
        rarity      TEXT DEFAULT 'common'
    );
    CREATE TABLE IF NOT EXISTS user_items (
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        PRIMARY KEY (user_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS topics (
        name        TEXT PRIMARY KEY,
        emoji       TEXT DEFAULT '?',
        is_official INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS follows (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id TEXT NOT NULL,
        followed_id TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(follower_id, followed_id)
    );
    CREATE TABLE IF NOT EXISTS direct_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id   TEXT NOT NULL,
        receiver_id TEXT NOT NULL,
        text        TEXT NOT NULL,
        read        INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS streaks (
        user_id      TEXT PRIMARY KEY,
        current      INTEGER DEFAULT 0,
        longest      INTEGER DEFAULT 0,
        last_active  TEXT DEFAULT '',
        shield_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS battles (
        id          TEXT PRIMARY KEY,
        challenger  TEXT NOT NULL,
        challenged  TEXT NOT NULL,
        topic       TEXT,
        status      TEXT DEFAULT 'pending',
        rounds_json TEXT DEFAULT '[]',
        winner      TEXT DEFAULT '',
        created     TEXT,
        expires     TEXT
    );
    CREATE TABLE IF NOT EXISTS parliament_votes (
        id       TEXT PRIMARY KEY,
        question TEXT NOT NULL,
        option_a TEXT,
        option_b TEXT,
        active   INTEGER DEFAULT 1,
        created  TEXT,
        ends     TEXT
    );
    CREATE TABLE IF NOT EXISTS parliament_responses (
        vote_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        choice  TEXT,
        country TEXT DEFAULT '',
        created TEXT,
        PRIMARY KEY (vote_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS dna_reports (
        user_id     TEXT PRIMARY KEY,
        report_json TEXT,
        generated   TEXT
    );
    CREATE TABLE IF NOT EXISTS tournaments (
        id     TEXT PRIMARY KEY,
        week   TEXT,
        status TEXT DEFAULT 'active',
        winner TEXT DEFAULT '',
        scores TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_posts_user   ON posts(user_id);
    CREATE INDEX IF NOT EXISTS idx_posts_topic  ON posts(topic);
    CREATE INDEX IF NOT EXISTS idx_eng_user     ON engagements(user_id);
    CREATE INDEX IF NOT EXISTS idx_dm_sender    ON direct_messages(sender_id);
    CREATE INDEX IF NOT EXISTS idx_dm_receiver  ON direct_messages(receiver_id);
    CREATE INDEX IF NOT EXISTS idx_flwr         ON follows(follower_id);
    CREATE INDEX IF NOT EXISTS idx_flwd         ON follows(followed_id);
    """)
    db.commit()
    _seed_db(db)
    db.close()

# ══════════════════════════════════════════════════════════════════
#  SEED DATA
# ══════════════════════════════════════════════════════════════════
SEED_POSTS_DATA = [
    ("technology","against","Social media has made us lonelier, not more connected. Every like is a substitute for a real conversation we are too anxious to have."),
    ("work","for","Remote work is making people less ambitious. The best career opportunities happen in spontaneous hallway conversations, not on Zoom calls."),
    ("education","against","University degrees are an expensive way to signal you can follow instructions. Most of what you learn there is irrelevant within 5 years."),
    ("health","for","Therapy has become a way for people to avoid taking responsibility for their own choices. Not every problem needs a diagnosis."),
    ("society","against","Hustle culture is a trauma response disguised as productivity. We have normalized exhaustion and called it ambition."),
    ("technology","for","AI will not take your job. It will take the job of someone who refuses to learn how to use AI. The threat is self-inflicted."),
    ("society","for","Cancel culture is not censorship. It is consequence culture. Powerful people finally facing accountability is not oppression."),
    ("science","against","We are overmedicating a generation of children who are simply responding normally to an abnormal world."),
]
SEED_TOPICS = [
    ("technology","💻"),("society","🌐"),("politics","🏛️"),("work","💼"),
    ("education","📚"),("health","❤️"),("art","🎨"),("science","🔬"),
]
SEED_MARKET = [
    ("m1","Echo Shield","Protects your streak for 1 day","🛡️",50,"common"),
    ("m2","Debate Master","Bold username in leaderboard","⚔️",200,"rare"),
    ("m3","Mind Reader","See who engaged your posts","🧠",150,"rare"),
    ("m4","Gold Avatar Ring","Golden ring around your avatar","👑",500,"epic"),
    ("m5","Verified Badge","Blue checkmark on your profile","✅",1000,"legendary"),
]
SEED_ROOMS_DATA = [
    ("AI will eliminate more jobs than it creates","technology"),
    ("Social media should be banned for under 16s","society"),
    ("Remote work is better for mental health","work"),
    ("University is no longer worth the cost","education"),
    ("Meat eating is ethically indefensible","ethics"),
]

def _seed_db(db):
    for name, emoji in SEED_TOPICS:
        db.execute("INSERT OR IGNORE INTO topics VALUES (?,?,1)", (name, emoji))
    for topic, stance, text in SEED_POSTS_DATA:
        pid = f"seed_{abs(hash(text)) % 99999}"
        db.execute("INSERT OR IGNORE INTO posts (id,user_id,username,avatar,text,topic,stance,likes,is_seed) VALUES (?,?,?,?,?,?,?,?,1)",
                   (pid,"echoroom_official","EchoRoom","ER",text,topic,stance,random.randint(40,400)))
    for mid, name, desc, emoji, price, rarity in SEED_MARKET:
        db.execute("INSERT OR IGNORE INTO market_items VALUES (?,?,?,?,?,?)",
                   (mid,name,desc,emoji,price,rarity))
    for question, category in SEED_ROOMS_DATA:
        rid  = f"room_{abs(hash(question)) % 9999}"
        code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
        db.execute("INSERT OR IGNORE INTO rooms (id,question,category,room_code,is_private) VALUES (?,?,?,?,0)",
                   (rid,question,category,code))
    db.execute("INSERT OR IGNORE INTO parliament_votes VALUES (?,?,?,?,1,?,?)",
               ("pv_default","Should AI systems have legal rights?","Yes","No",
                datetime.datetime.now().isoformat(),
                (datetime.datetime.now()+datetime.timedelta(days=7)).isoformat()))
    db.commit()

# ══════════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title="ECHO ROOM API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
init_db()

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════
def sanitize(text: str, maxlen: int = 500) -> str:
    return text.strip()[:maxlen]

def get_rank(total: int) -> str:
    if total >= 500: return "Enlightened"
    if total >= 300: return "Open Mind"
    if total >= 150: return "Curious"
    if total >= 50:  return "Awakening"
    return "Echo Chamber"

def _get_rank(total: int) -> str:
    return get_rank(total)

def _next_rank(total: int) -> dict:
    for t, name in [(50,"Awakening"),(150,"Curious"),(300,"Open Mind"),(500,"Enlightened")]:
        if total < t:
            return {"name": name, "points_needed": t - total}
    return {"name": "Max rank", "points_needed": 0}

# ══════════════════════════════════════════════════════════════════
#  AI
# ══════════════════════════════════════════════════════════════════
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OLLAMA_URL     = "http://localhost:11434/api/generate"
OLLAMA_MODEL   = "llama3"

def ask_ai(prompt: str, system: str = "") -> str:
    if CLAUDE_API_KEY:
        try:
            r = req.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": CLAUDE_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 512,
                      "system": system or "You are the ECHO ROOM AI.",
                      "messages": [{"role":"user","content":prompt}]},
                timeout=30)
            return r.json()["content"][0]["text"].strip()
        except: pass
    try:
        r = req.post(OLLAMA_URL, json={"model":OLLAMA_MODEL,"prompt":prompt,"system":system,"stream":False}, timeout=60)
        return r.json().get("response","").strip()
    except: return ""

def ask_ai_json(prompt: str, system: str = "") -> dict:
    raw = ask_ai(prompt, system)
    try:
        raw = re.sub(r"```json|```","",raw).strip()
        return json.loads(raw)
    except: return {}

# ══════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ══════════════════════════════════════════════════════════════════
def get_user(authorization: str = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(401, "Not authenticated")
    token   = authorization.replace("Bearer ","").strip()
    db      = get_db()
    session = db.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    if not session:
        db.close(); raise HTTPException(401, "Invalid token")
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    if not user: raise HTTPException(401, "User not found")
    return dict(user)

# ══════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════
class AuthReq(BaseModel):
    username: str
    email:    str
    password: str

class LoginReq(BaseModel):
    email:    str
    password: str

@app.post("/auth/register")
def register(r: AuthReq):
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email=?", (r.email,)).fetchone():
        db.close(); raise HTTPException(400, "Email already registered")
    uid = str(uuid.uuid4()); token = str(uuid.uuid4())
    avatar = r.username[:2].upper()
    db.execute("INSERT INTO users (id,username,email,password,avatar,echo_coins) VALUES (?,?,?,?,?,50)",
               (uid,r.username,r.email,r.password,avatar))
    db.execute("INSERT INTO scores (user_id) VALUES (?)", (uid,))
    db.execute("INSERT INTO sessions (token,user_id) VALUES (?,?)", (token,uid))
    db.commit(); db.close()
    return {"token":token,"user_id":uid,"username":r.username,
            "echo_coins":50,"plus":0,"theme_color":"purple","avatar_color":"#8b5cf6","avatar":avatar}

@app.post("/auth/login")
def login(r: LoginReq):
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE email=? AND password=?", (r.email,r.password)).fetchone()
    if not user:
        db.close(); raise HTTPException(401, "Invalid credentials")
    token = str(uuid.uuid4())
    db.execute("INSERT INTO sessions (token,user_id) VALUES (?,?)", (token,user["id"]))
    db.commit(); db.close()
    u = dict(user)
    return {"token":token,"user_id":u["id"],"username":u["username"],
            "echo_coins":u.get("echo_coins",0),"plus":u.get("plus",0),
            "theme_color":u.get("theme_color","purple"),"avatar_color":u.get("avatar_color","#8b5cf6"),"avatar":u["avatar"]}

@app.post("/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token=?", (authorization.replace("Bearer ","").strip(),))
        db.commit(); db.close()
    return {"status":"logged_out"}

@app.get("/auth/verify")
def verify_token(user: dict = Depends(get_user)):
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    db.close()
    total = (sc["total"] if sc else 0) or 0
    return {"valid":True,"user_id":user["id"],"username":user["username"],
            "echo_coins":user.get("echo_coins",0),"plus":user.get("plus",0),
            "theme_color":user.get("theme_color","purple"),"avatar_color":user.get("avatar_color","#8b5cf6"),
            "avatar":user.get("avatar","?"),"rank":get_rank(total)}

# ══════════════════════════════════════════════════════════════════
#  TOPICS
# ══════════════════════════════════════════════════════════════════
@app.get("/topics")
def get_topics():
    db   = get_db()
    rows = db.execute("SELECT * FROM topics WHERE is_official=1").fetchall()
    db.close()
    return {"topics":[dict(r) for r in rows]}

# ══════════════════════════════════════════════════════════════════
#  FEED & POSTS
# ══════════════════════════════════════════════════════════════════
@app.get("/feed")
def get_feed(topic: str = "", user: dict = Depends(get_user)):
    db = get_db()
    if topic:
        rows = db.execute("SELECT * FROM posts WHERE topic=? ORDER BY timestamp DESC LIMIT 30", (topic,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM posts ORDER BY timestamp DESC LIMIT 30").fetchall()
    db.close()
    return {"posts":[dict(r) for r in rows]}

class PostReq(BaseModel):
    text:   str
    topic:  str
    stance: str

@app.post("/post")
def create_post(r: PostReq, user: dict = Depends(get_user)):
    pid = str(uuid.uuid4())[:8]
    db  = get_db()
    db.execute("INSERT INTO posts (id,user_id,username,avatar,text,topic,stance) VALUES (?,?,?,?,?,?,?)",
               (pid,user["id"],user["username"],user.get("avatar","?"),sanitize(r.text,500),r.topic,r.stance))
    db.commit(); db.close()
    return {"status":"ok","post_id":pid}

# ══════════════════════════════════════════════════════════════════
#  ENGAGE
# ══════════════════════════════════════════════════════════════════
class EngageReq(BaseModel):
    response: str
    post_id:  str

@app.post("/engage/{post_id}")
def engage(post_id: str, r: EngageReq, user: dict = Depends(get_user)):
    db   = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        db.close(); raise HTTPException(404, "Post not found")

    result = ask_ai_json(
        f'Score this engagement:\nOriginal opinion: "{post["text"]}"\nUser response: "{r.response}"\nReturn ONLY JSON: {{"quality":0-10,"empathy":0-10,"logic":0-10,"openness":0-10,"mind_shift":0-10,"total":0-50,"feedback":"one sentence","badge_earned":null}}',
        "You are ECHO ROOM AI judge. Score open-mindedness strictly. Return JSON only.")

    if not result or "total" not in result:
        result = {"quality":5,"empathy":5,"logic":5,"openness":5,"mind_shift":3,"total":23,
                  "feedback":"Decent engagement. Try to acknowledge one point from the other side.","badge_earned":None}

    total_score  = int(result.get("total",0))
    mind_changed = 1 if result.get("mind_shift",0) >= 7 else 0
    coins_earned = max(5, total_score // 5)
    eid          = str(uuid.uuid4())[:8]

    db.execute("INSERT INTO engagements (id,user_id,post_id,response,score_json,total_score,mind_changed,coins_earned) VALUES (?,?,?,?,?,?,?,?)",
               (eid,user["id"],post_id,sanitize(r.response,1000),json.dumps(result),total_score,mind_changed,coins_earned))
    db.execute("""INSERT INTO scores (user_id,total,engagements,mind_changes) VALUES (?,?,1,?)
                  ON CONFLICT(user_id) DO UPDATE SET total=total+?,engagements=engagements+1,mind_changes=mind_changes+?""",
               (user["id"],total_score,mind_changed,total_score,mind_changed))
    db.execute("UPDATE users SET echo_coins=echo_coins+? WHERE id=?", (coins_earned,user["id"]))
    _update_streak(user["id"],db)
    db.commit()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    db.close()
    return {"status":"ok","score":result,"coins_earned":coins_earned,
            "your_total":(sc["total"] if sc else 0) or 0,
            "your_rank":get_rank((sc["total"] if sc else 0) or 0)}

# ══════════════════════════════════════════════════════════════════
#  SCORE & LEADERBOARD
# ══════════════════════════════════════════════════════════════════
@app.get("/score")
def get_score(user: dict = Depends(get_user)):
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    db.close()
    total = (sc["total"] if sc else 0) or 0
    return {"score":total,"rank":get_rank(total),
            "engagements":(sc["engagements"] if sc else 0) or 0,
            "mind_changes":(sc["mind_changes"] if sc else 0) or 0,
            "next_rank":_next_rank(total)}

@app.get("/leaderboard")
def leaderboard():
    db   = get_db()
    rows = db.execute("""SELECT u.id,u.username,u.avatar,u.avatar_color,u.plus,s.total,s.engagements,s.mind_changes
                         FROM scores s JOIN users u ON s.user_id=u.id ORDER BY s.total DESC LIMIT 50""").fetchall()
    db.close()
    out = []
    for i,r in enumerate(rows):
        d = dict(r); d["rank"] = get_rank(d["total"] or 0); d["total"] = d["total"] or 0
        out.append(d)
    return {"leaderboard":out}

# ══════════════════════════════════════════════════════════════════
#  ROOMS
# ══════════════════════════════════════════════════════════════════
class CreateRoomReq(BaseModel):
    question:   str
    topic:      str  = "society"
    is_private: bool = False

class RoomSpeakReq(BaseModel):
    text: str

@app.get("/rooms")
def list_rooms(user: dict = Depends(get_user)):
    db   = get_db()
    rows = db.execute("""SELECT r.*,COUNT(m.id) as message_count FROM rooms r
                         LEFT JOIN room_messages m ON m.room_id=r.id GROUP BY r.id ORDER BY r.created DESC LIMIT 50""").fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r); d["messages"] = []; result.append(d)
    return {"rooms":result}

@app.post("/rooms")
def create_room(r: CreateRoomReq, user: dict = Depends(get_user)):
    rid  = str(uuid.uuid4())[:8]
    code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',k=6))
    db   = get_db()
    db.execute("INSERT INTO rooms (id,question,category,room_code,is_private,created_by) VALUES (?,?,?,?,?,?)",
               (rid,sanitize(r.question,300),r.topic,code,1 if r.is_private else 0,user["id"]))
    db.commit(); db.close()
    return {"room_id":rid,"room_code":code,"question":r.question,"status":"created"}

@app.post("/rooms/join/{code}")
def join_room_by_code(code: str, user: dict = Depends(get_user)):
    db   = get_db()
    room = db.execute("SELECT * FROM rooms WHERE room_code=?", (code.upper(),)).fetchone()
    if not room:
        db.close(); raise HTTPException(404, "Room not found")
    msgs = db.execute("SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 50", (room["id"],)).fetchall()
    db.close()
    d = dict(room); d["messages"] = [dict(m) for m in msgs]
    return {"status":"joined","room":d,"question":room["question"]}

@app.post("/rooms/{room_id}/speak")
def speak_in_room(room_id: str, r: RoomSpeakReq, user: dict = Depends(get_user)):
    db   = get_db()
    room = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        db.close(); raise HTTPException(404, "Room not found")
    mod = ask_ai_json(
        f'Evaluate debate contribution.\nTopic: "{room["question"]}"\nMessage: "{r.text}"\nReturn ONLY JSON: {{"approved":true,"quality_score":5,"ai_response":"one sentence","flag":null}}',
        "You are a debate moderator. Return JSON only.")
    if not mod: mod = {"approved":True,"quality_score":5,"ai_response":"Interesting point. What evidence supports this?","flag":None}
    mid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO room_messages (id,room_id,user_id,username,avatar,text) VALUES (?,?,?,?,?,?)",
               (mid,room_id,user["id"],user["username"],user.get("avatar","?"),sanitize(r.text,500)))
    db.execute("UPDATE rooms SET message_count=message_count+1 WHERE id=?", (room_id,))
    db.commit()
    msgs = db.execute("SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 50", (room_id,)).fetchall()
    db.close()
    return {"message":{"id":mid,"user_id":user["id"],"username":user["username"],"avatar":user.get("avatar","?"),"text":r.text,"timestamp":datetime.datetime.now().isoformat()},
            "ai_response":mod.get("ai_response",""),"room_messages":[dict(m) for m in msgs]}

# ══════════════════════════════════════════════════════════════════
#  REELS
# ══════════════════════════════════════════════════════════════════
class ReelReq(BaseModel):
    video_url: str
    caption:   str = ""
    topic:     str = "society"

@app.get("/reels")
def get_reels(user: dict = Depends(get_user)):
    db   = get_db()
    rows = db.execute("SELECT * FROM reels ORDER BY created DESC LIMIT 50").fetchall()
    db.close()
    return {"reels":[dict(r) for r in rows]}

@app.post("/reels")
def post_reel(r: ReelReq, user: dict = Depends(get_user)):
    rid = str(uuid.uuid4())[:8]; db = get_db()
    db.execute("INSERT INTO reels (id,user_id,username,avatar,video_url,caption,topic) VALUES (?,?,?,?,?,?,?)",
               (rid,user["id"],user["username"],user.get("avatar","?"),r.video_url,sanitize(r.caption,200),r.topic))
    db.execute("UPDATE users SET echo_coins=echo_coins+10 WHERE id=?", (user["id"],))
    db.commit(); db.close()
    return {"status":"posted","reel_id":rid,"coins_earned":10}

@app.post("/reels/{reel_id}/like")
def like_reel(reel_id: str, user: dict = Depends(get_user)):
    db = get_db()
    if db.execute("SELECT 1 FROM reel_likes WHERE reel_id=? AND user_id=?", (reel_id,user["id"])).fetchone():
        db.execute("DELETE FROM reel_likes WHERE reel_id=? AND user_id=?", (reel_id,user["id"]))
        db.execute("UPDATE reels SET likes=MAX(0,likes-1) WHERE id=?", (reel_id,))
        liked = False
    else:
        db.execute("INSERT OR IGNORE INTO reel_likes VALUES (?,?)", (reel_id,user["id"]))
        db.execute("UPDATE reels SET likes=likes+1 WHERE id=?", (reel_id,))
        liked = True
    db.commit(); db.close()
    return {"liked":liked}

# ══════════════════════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════════════════════
class UpdateProfileReq(BaseModel):
    bio: str = ""

def _build_profile(user_id: str, viewer_id: str, db) -> dict:
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user: raise HTTPException(404, "User not found")
    u   = dict(user)
    sc  = db.execute("SELECT * FROM scores WHERE user_id=?", (user_id,)).fetchone()
    total        = (sc["total"]        if sc else 0) or 0
    engagements  = (sc["engagements"]  if sc else 0) or 0
    mind_changes = (sc["mind_changes"] if sc else 0) or 0
    streak_row   = db.execute("SELECT current FROM streaks WHERE user_id=?", (user_id,)).fetchone()
    streak       = (streak_row["current"] if streak_row else 0) or 0
    followers    = (db.execute("SELECT COUNT(*) FROM follows WHERE followed_id=?", (user_id,)).fetchone() or [0])[0]
    following    = (db.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (user_id,)).fetchone() or [0])[0]
    is_following = False
    if viewer_id and viewer_id != user_id:
        is_following = db.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (viewer_id,user_id)).fetchone() is not None
    posts = db.execute("SELECT id,text,topic,stance,timestamp FROM posts WHERE user_id=? ORDER BY timestamp DESC LIMIT 20", (user_id,)).fetchall()
    owned = db.execute("SELECT i.name,i.emoji FROM market_items i JOIN user_items ui ON ui.item_id=i.id WHERE ui.user_id=?", (user_id,)).fetchall()
    badges = json.loads((sc["badges"] if sc else "[]") or "[]")
    return {"id":u["id"],"username":u["username"],"avatar":u["avatar"],"avatar_color":u.get("avatar_color","#8b5cf6"),
            "bio":u.get("bio") or "","plus":u.get("plus",0),"theme_color":u.get("theme_color","purple"),
            "echo_coins":u.get("echo_coins",0),"score":total,"engagements":engagements,"mind_changes":mind_changes,
            "streak":streak,"rank":get_rank(total),"next_rank":_next_rank(total),"followers":followers,
            "following_count":following,"is_following":is_following,
            "posts":[dict(p) for p in posts],"owned_items":[dict(o) for o in owned],"badges":badges}

@app.get("/profile/me/full")
def get_my_profile(user: dict = Depends(get_user)):
    db = get_db(); result = _build_profile(user["id"],user["id"],db); db.close(); return result

@app.get("/profile/{user_id}/full")
def get_user_profile(user_id: str, user: dict = Depends(get_user)):
    db = get_db(); result = _build_profile(user_id,user["id"],db); db.close(); return result

@app.put("/profile/update")
def update_profile(r: UpdateProfileReq, user: dict = Depends(get_user)):
    db = get_db()
    db.execute("UPDATE users SET bio=? WHERE id=?", (sanitize(r.bio,300),user["id"]))
    db.commit(); db.close()
    return {"status":"updated"}

# ══════════════════════════════════════════════════════════════════
#  FOLLOW SYSTEM
# ══════════════════════════════════════════════════════════════════
@app.post("/follow/{user_id}")
def toggle_follow(user_id: str, user: dict = Depends(get_user)):
    if user_id == user["id"]: raise HTTPException(400, "Cannot follow yourself")
    db = get_db()
    if db.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (user["id"],user_id)).fetchone():
        db.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (user["id"],user_id))
        db.commit(); db.close(); return {"following":False}
    db.execute("INSERT OR IGNORE INTO follows (follower_id,followed_id) VALUES (?,?)", (user["id"],user_id))
    db.commit(); db.close(); return {"following":True}

# ══════════════════════════════════════════════════════════════════
#  DIRECT MESSAGES
# ══════════════════════════════════════════════════════════════════
class DMReq(BaseModel):
    text: str

@app.get("/dms")
def list_dms(user: dict = Depends(get_user)):
    uid = user["id"]; db = get_db()
    rows = db.execute("""
        SELECT CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END AS other_id, MAX(created_at) AS last_time
        FROM direct_messages WHERE sender_id=? OR receiver_id=? GROUP BY other_id ORDER BY last_time DESC
    """, (uid,uid,uid)).fetchall()
    convos = []
    for row in rows:
        oid   = row["other_id"]
        other = db.execute("SELECT username,avatar,avatar_color FROM users WHERE id=?", (oid,)).fetchone()
        last  = db.execute("SELECT text FROM direct_messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY created_at DESC LIMIT 1", (uid,oid,oid,uid)).fetchone()
        unread= (db.execute("SELECT COUNT(*) FROM direct_messages WHERE sender_id=? AND receiver_id=? AND read=0", (oid,uid)).fetchone() or [0])[0]
        convos.append({"other_user_id":oid,"other_username":other["username"] if other else "Unknown",
                       "other_avatar":other["avatar"] if other else "?","other_avatar_color":other["avatar_color"] if other else "#8b5cf6",
                       "last_message":last["text"] if last else "","last_time":row["last_time"],"unread_count":unread or 0})
    db.close(); return {"conversations":convos}

@app.get("/dms/{other_user_id}")
def get_dm_thread(other_user_id: str, user: dict = Depends(get_user)):
    uid = user["id"]; db = get_db()
    msgs = db.execute("""SELECT dm.*,u.username AS sender_username,u.avatar AS sender_avatar
                         FROM direct_messages dm JOIN users u ON u.id=dm.sender_id
                         WHERE (dm.sender_id=? AND dm.receiver_id=?) OR (dm.sender_id=? AND dm.receiver_id=?)
                         ORDER BY dm.created_at ASC""", (uid,other_user_id,other_user_id,uid)).fetchall()
    db.execute("UPDATE direct_messages SET read=1 WHERE sender_id=? AND receiver_id=? AND read=0", (other_user_id,uid))
    db.commit()
    other = db.execute("SELECT id,username,avatar,avatar_color FROM users WHERE id=?", (other_user_id,)).fetchone()
    db.close()
    def fmt(r): return {"id":r["id"],"sender_id":r["sender_id"],"sender_username":r["sender_username"],
                        "sender_avatar":r["sender_avatar"],"text":r["text"],"read":bool(r["read"]),
                        "created_at":r["created_at"],"is_mine":r["sender_id"]==uid}
    return {"messages":[fmt(m) for m in msgs],"other_user":dict(other) if other else None}

@app.post("/dms/{other_user_id}")
def send_dm(other_user_id: str, r: DMReq, user: dict = Depends(get_user)):
    if other_user_id == user["id"]: raise HTTPException(400, "Cannot DM yourself")
    text = sanitize(r.text,1000)
    if not text: raise HTTPException(400, "Empty message")
    db    = get_db()
    other = db.execute("SELECT id FROM users WHERE id=?", (other_user_id,)).fetchone()
    if not other: db.close(); raise HTTPException(404, "User not found")
    db.execute("INSERT INTO direct_messages (sender_id,receiver_id,text) VALUES (?,?,?)", (user["id"],other_user_id,text))
    db.commit()
    msg = db.execute("""SELECT dm.*,u.username AS sender_username,u.avatar AS sender_avatar
                        FROM direct_messages dm JOIN users u ON u.id=dm.sender_id
                        WHERE dm.sender_id=? AND dm.receiver_id=? ORDER BY dm.created_at DESC LIMIT 1""",
                     (user["id"],other_user_id)).fetchone()
    db.close()
    m = dict(msg)
    return {"message":{"id":m["id"],"sender_id":m["sender_id"],"sender_username":m["sender_username"],
                       "sender_avatar":m["sender_avatar"],"text":m["text"],"read":False,
                       "created_at":m["created_at"],"is_mine":True},"status":"sent"}

# ══════════════════════════════════════════════════════════════════
#  MARKET
# ══════════════════════════════════════════════════════════════════
class BuyReq(BaseModel):
    item_id: str

@app.get("/market")
def get_market(user: dict = Depends(get_user)):
    db    = get_db()
    items = db.execute("SELECT * FROM market_items ORDER BY price ASC").fetchall()
    owned = {r["item_id"] for r in db.execute("SELECT item_id FROM user_items WHERE user_id=?", (user["id"],)).fetchall()}
    db.close()
    return {"items":[{**dict(i),"owned":i["id"] in owned} for i in items],"your_coins":user.get("echo_coins",0)}

@app.post("/market/buy")
def buy_item(r: BuyReq, user: dict = Depends(get_user)):
    db   = get_db()
    item = db.execute("SELECT * FROM market_items WHERE id=?", (r.item_id,)).fetchone()
    if not item: db.close(); raise HTTPException(404, "Item not found")
    if db.execute("SELECT 1 FROM user_items WHERE user_id=? AND item_id=?", (user["id"],r.item_id)).fetchone():
        db.close(); raise HTTPException(400, "Already owned")
    coins = (db.execute("SELECT echo_coins FROM users WHERE id=?", (user["id"],)).fetchone() or {}).get("echo_coins",0)
    if coins < item["price"]: db.close(); raise HTTPException(400, "Not enough coins")
    db.execute("UPDATE users SET echo_coins=echo_coins-? WHERE id=?", (item["price"],user["id"]))
    db.execute("INSERT INTO user_items VALUES (?,?)", (user["id"],r.item_id))
    db.commit()
    coins_left = (db.execute("SELECT echo_coins FROM users WHERE id=?", (user["id"],)).fetchone() or {}).get("echo_coins",0)
    db.close(); return {"status":"purchased","coins_left":coins_left}

# ══════════════════════════════════════════════════════════════════
#  STREAK
# ══════════════════════════════════════════════════════════════════
def _streak_reward(day: int) -> dict:
    if day >= 30: return {"coins":100,"badge":"streak_legend"}
    if day >= 14: return {"coins":50,"badge":"streak_master"}
    if day >= 7:  return {"coins":25,"badge":"streak_week"}
    if day >= 3:  return {"coins":10,"badge":None}
    return {"coins":3,"badge":None}

def _update_streak(user_id: str, db):
    today     = datetime.date.today().isoformat()
    yesterday = str(datetime.date.today() - datetime.timedelta(days=1))
    row       = db.execute("SELECT * FROM streaks WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO streaks VALUES (?,1,1,?,0)", (user_id,today))
        db.execute("UPDATE users SET echo_coins=echo_coins+3 WHERE id=?", (user_id,)); return
    last = (row["last_active"] or "")[:10]
    if last == today: return
    current = row["current"] or 0; longest = row["longest"] or 0
    if last == yesterday: current += 1
    elif (row["shield_count"] or 0) > 0:
        db.execute("UPDATE streaks SET shield_count=shield_count-1 WHERE user_id=?", (user_id,))
        current += 1
    else: current = 1
    longest = max(longest, current)
    reward  = _streak_reward(current)
    db.execute("UPDATE streaks SET current=?,longest=?,last_active=? WHERE user_id=?", (current,longest,today,user_id))
    db.execute("UPDATE users SET echo_coins=echo_coins+? WHERE id=?", (reward["coins"],user_id))

@app.get("/streak")
def get_streak(user: dict = Depends(get_user)):
    db = get_db(); row = db.execute("SELECT * FROM streaks WHERE user_id=?", (user["id"],)).fetchone(); db.close()
    if not row: return {"current":0,"longest":0,"shield_count":0,"status":"no streak yet"}
    today = datetime.date.today().isoformat(); last = (row["last_active"] or "")[:10]; current = row["current"] or 0
    status = "done_today" if last==today else "active" if last==str(datetime.date.today()-datetime.timedelta(days=1)) else "broken"
    if status == "broken": current = 0
    return {"current":current,"longest":row["longest"] or 0,"shield_count":row["shield_count"] or 0,
            "last_active":last,"status":status,"next_reward":_streak_reward(current+1)}

# ══════════════════════════════════════════════════════════════════
#  DNA REPORT
# ══════════════════════════════════════════════════════════════════
@app.get("/dna")
def get_dna(user: dict = Depends(get_user)):
    db  = get_db()
    sc  = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    eng = (sc["engagements"] if sc else 0) or 0
    if eng < 5: db.close(); return {"ready":False,"engagements":eng,"needed":5}
    cached = db.execute("SELECT * FROM dna_reports WHERE user_id=?", (user["id"],)).fetchone()
    if cached:
        try:
            if (datetime.datetime.now()-datetime.datetime.fromisoformat(cached["generated"])).days < 7:
                db.close(); return {"ready":True,"report":json.loads(cached["report_json"]),"generated":cached["generated"]}
        except: pass
    engs = db.execute("SELECT score_json FROM engagements WHERE user_id=? ORDER BY timestamp DESC LIMIT 20", (user["id"],)).fetchall()
    db.close()
    scores_data = [json.loads(e["score_json"]) for e in engs if e["score_json"]]
    avg = lambda k: round(sum(s.get(k,5) for s in scores_data)/max(len(scores_data),1),1)
    traits = {"quality":avg("quality"),"empathy":avg("empathy"),"logic":avg("logic"),"openness":avg("openness")}
    dominant = max(traits, key=traits.get)
    pmap = {"quality":("The Architect","🏛️","You build arguments with precision and care."),
            "empathy":("The Empath","💙","You feel the human side of every debate."),
            "logic":("The Philosopher","🔬","You cut through emotion to find truth."),
            "openness":("The Explorer","🌍","You genuinely seek to understand others.")}
    ptype,icon,desc = pmap[dominant]
    report = {"personality_type":ptype,"icon":icon,"description":desc,"scores":traits,"total_debates":eng,
              "rank":get_rank((sc["total"] if sc else 0) or 0),
              "strengths":[f"Your {dominant} score is your superpower","You consistently engage rather than dismiss"],
              "biases":["No significant biases detected" if all(v>=4 for v in traits.values()) else "Work on your lower-scoring dimensions"],
              "debate_style":"Balanced","growth_tip":"Keep engaging — you are growing every day"}
    db2 = get_db()
    db2.execute("INSERT OR REPLACE INTO dna_reports VALUES (?,?,?)", (user["id"],json.dumps(report),datetime.datetime.now().isoformat()))
    db2.commit(); db2.close()
    return {"ready":True,"report":report,"generated":datetime.datetime.now().isoformat()}

# ══════════════════════════════════════════════════════════════════
#  PARLIAMENT
# ══════════════════════════════════════════════════════════════════
class ParliamentVoteReq(BaseModel):
    vote_id: str
    choice:  str
    country: Optional[str] = ""

@app.get("/parliament")
def get_parliament():
    db   = get_db()
    vote = db.execute("SELECT * FROM parliament_votes WHERE active=1 ORDER BY created DESC LIMIT 1").fetchone()
    if not vote: db.close(); return {"active":False}
    total   = (db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=?", (vote["id"],)).fetchone() or [0])[0]
    a_count = (db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='a'", (vote["id"],)).fetchone() or [0])[0]
    b_count = (db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='b'", (vote["id"],)).fetchone() or [0])[0]
    db.close()
    return {"active":True,"vote":dict(vote),"total":total,"a_count":a_count,"b_count":b_count,
            "a_pct":round(a_count/max(total,1)*100),"b_pct":round(b_count/max(total,1)*100),"ends":vote["ends"]}

@app.post("/parliament/vote")
def parliament_vote(r: ParliamentVoteReq, user: dict = Depends(get_user)):
    if r.choice not in ["a","b"]: raise HTTPException(400, "Choice must be a or b")
    db = get_db()
    if not db.execute("SELECT 1 FROM parliament_votes WHERE id=? AND active=1", (r.vote_id,)).fetchone():
        db.close(); raise HTTPException(404, "Vote not found")
    if db.execute("SELECT 1 FROM parliament_responses WHERE vote_id=? AND user_id=?", (r.vote_id,user["id"])).fetchone():
        db.close(); raise HTTPException(400, "Already voted")
    db.execute("INSERT INTO parliament_responses VALUES (?,?,?,?,?)",
               (r.vote_id,user["id"],r.choice,r.country or "",datetime.datetime.now().isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins+15 WHERE id=?", (user["id"],))
    db.commit(); db.close()
    return {"status":"voted","coins_earned":15}

# ══════════════════════════════════════════════════════════════════
#  SHARE CARD
# ══════════════════════════════════════════════════════════════════
@app.get("/sharecard")
def get_share_card(user: dict = Depends(get_user)):
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    sr = db.execute("SELECT current FROM streaks WHERE user_id=?", (user["id"],)).fetchone()
    db.close()
    total = (sc["total"] if sc else 0) or 0
    return {"username":user["username"],"avatar":user.get("avatar","?"),"avatar_color":user.get("avatar_color","#8b5cf6"),
            "rank":get_rank(total),"score":total,"engagements":(sc["engagements"] if sc else 0) or 0,
            "mind_changes":(sc["mind_changes"] if sc else 0) or 0,"streak":(sr["current"] if sr else 0) or 0,
            "plus":user.get("plus",0),"theme_color":user.get("theme_color","purple"),"tagline":"Expand your mind. Earn your rank."}

# ══════════════════════════════════════════════════════════════════
#  BATTLES
# ══════════════════════════════════════════════════════════════════
class BattleReq(BaseModel):
    challenged_id: str
    topic:         str

class BattleRoundReq(BaseModel):
    argument: str

@app.post("/battles/challenge")
def challenge_battle(r: BattleReq, user: dict = Depends(get_user)):
    if r.challenged_id == user["id"]: raise HTTPException(400, "Cannot challenge yourself")
    db = get_db()
    target = db.execute("SELECT username FROM users WHERE id=?", (r.challenged_id,)).fetchone()
    if not target: db.close(); raise HTTPException(404, "User not found")
    bid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO battles VALUES (?,?,?,?,?,?,?,?,?)",
               (bid,user["id"],r.challenged_id,r.topic,"pending","[]","",
                datetime.datetime.now().isoformat(),(datetime.datetime.now()+datetime.timedelta(hours=24)).isoformat()))
    db.commit(); db.close()
    return {"battle_id":bid,"status":"challenge_sent","message":f"Challenge sent to {target['username']}"}

@app.post("/battles/{battle_id}/accept")
def accept_battle(battle_id: str, user: dict = Depends(get_user)):
    db = get_db(); battle = db.execute("SELECT * FROM battles WHERE id=?", (battle_id,)).fetchone()
    if not battle or battle["challenged"] != user["id"]: db.close(); raise HTTPException(404, "Battle not found")
    db.execute("UPDATE battles SET status='active' WHERE id=?", (battle_id,))
    db.commit(); db.close(); return {"status":"battle_started","topic":battle["topic"]}

@app.post("/battles/{battle_id}/argue")
def submit_argument(battle_id: str, r: BattleRoundReq, user: dict = Depends(get_user)):
    db = get_db(); battle = db.execute("SELECT * FROM battles WHERE id=?", (battle_id,)).fetchone()
    if not battle or battle["status"] != "active": db.close(); raise HTTPException(404, "Battle not active")
    if user["id"] not in [battle["challenger"],battle["challenged"]]: db.close(); raise HTTPException(403, "Not a participant")
    argument = sanitize(r.argument,1000); rounds = json.loads(battle["rounds_json"] or "[]")
    role = "challenger" if user["id"]==battle["challenger"] else "challenged"
    sr = ask_ai_json(f'Score debate argument on "{battle["topic"]}": "{argument}"\nReturn ONLY JSON: {{"score":0-10,"feedback":"one sentence"}}', "Strict debate judge.")
    if not sr: sr = {"score":5,"feedback":"Decent argument."}
    rounds.append({"user_id":user["id"],"username":user["username"],"role":role,"argument":argument,
                   "score":sr.get("score",5),"feedback":sr.get("feedback",""),"round":len(rounds)+1})
    winner = ""; status = "active"
    if len(rounds) >= 10:
        c  = sum(x["score"] for x in rounds if x["role"]=="challenger")
        ch = sum(x["score"] for x in rounds if x["role"]=="challenged")
        winner = battle["challenger"] if c > ch else battle["challenged"]
        status = "complete"
        db.execute("UPDATE users SET echo_coins=echo_coins+100 WHERE id=?", (winner,))
    db.execute("UPDATE battles SET rounds_json=?,status=?,winner=? WHERE id=?", (json.dumps(rounds),status,winner,battle_id))
    db.commit(); db.close()
    return {"status":status,"round":len(rounds),"score":sr,"winner":winner,"rounds":rounds}

@app.get("/battles/my/list")
def my_battles(user: dict = Depends(get_user)):
    db = get_db()
    rows = db.execute("SELECT * FROM battles WHERE challenger=? OR challenged=? ORDER BY created DESC LIMIT 20",
                      (user["id"],user["id"])).fetchall()
    db.close(); return {"battles":[dict(b) for b in rows]}

# ══════════════════════════════════════════════════════════════════
#  TOURNAMENT
# ══════════════════════════════════════════════════════════════════
@app.get("/tournament")
def get_tournament():
    week = datetime.date.today().strftime("%Y-W%V"); db = get_db()
    if not db.execute("SELECT 1 FROM tournaments WHERE week=?", (week,)).fetchone():
        db.execute("INSERT INTO tournaments VALUES (?,?,?,?,?)", (str(uuid.uuid4())[:8],week,"active","","{}"))
        db.commit()
    top = db.execute("SELECT u.username,u.avatar,u.avatar_color,s.total FROM scores s JOIN users u ON s.user_id=u.id ORDER BY s.total DESC LIMIT 10").fetchall()
    db.close()
    return {"week":week,"days_left":7-datetime.date.today().weekday(),"top_10":[dict(r) for r in top],
            "prize":{"coins":1000,"badge":"tournament_champion"}}

# ══════════════════════════════════════════════════════════════════
#  ROOT
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def root():
    db = get_db()
    users = (db.execute("SELECT COUNT(*) FROM users").fetchone() or [0])[0]
    posts = (db.execute("SELECT COUNT(*) FROM posts").fetchone() or [0])[0]
    db.close()
    return {"app":"ECHO ROOM","version":"2.0","tagline":"Expand your mind. Earn your rank.","users":users,"posts":posts}

if __name__ == "__main__":
    print("ECHO ROOM API v2 starting...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT",8000)), log_level="warning")

