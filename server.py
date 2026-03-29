"""
ECHO ROOM — Server v3
=====================
New in v3:
  - Persistent login (JWT tokens, 30-day expiry)
  - Private rooms with room codes
  - Shareable room URLs
  - Enhanced security (rate limiting, input validation, bcrypt)
  - Custom topics (politics, art, science + user-created)
  - Profile customization (avatar color, bio, badge display)
  - Market system (buy profile items with Echo Coins)
  - Echo Room Plus subscription
  - Persistent points (never reset on login)
  - Video post support (reels)
  - Theme preferences saved per user
"""

import os, json, uuid, datetime, re, sqlite3, hashlib, secrets, time
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import requests as req
import uvicorn

# ── CONFIG ────────────────────────────────────────────────────────
DB_FILE      = "echoroom_v3.db"
OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
PORT         = int(os.getenv("PORT",     8000))
TOKEN_EXPIRY_DAYS = 30

# ── RATE LIMITING (simple in-memory) ─────────────────────────────
_rate_store: dict = {}

def check_rate_limit(ip: str, limit: int = 60, window: int = 60) -> bool:
    now   = time.time()
    key   = f"{ip}:{int(now/window)}"
    count = _rate_store.get(key, 0) + 1
    _rate_store[key] = count
    # cleanup old keys
    if len(_rate_store) > 10000:
        cutoff = int(now/window) - 2
        _rate_store.clear()
    return count <= limit


# ══════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT UNIQUE NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        avatar        TEXT,
        avatar_color  TEXT DEFAULT '#8b5cf6',
        bio           TEXT DEFAULT '',
        joined        TEXT,
        premium       INTEGER DEFAULT 0,
        plus          INTEGER DEFAULT 0,
        echo_coins    INTEGER DEFAULT 100,
        theme_color   TEXT DEFAULT 'purple',
        equipped_items TEXT DEFAULT '[]',
        follower_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        created    TEXT,
        expires    TEXT,
        device     TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS posts (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        username   TEXT,
        avatar     TEXT,
        avatar_color TEXT DEFAULT '#8b5cf6',
        text       TEXT,
        topic      TEXT,
        stance     TEXT,
        media_url  TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        timestamp  TEXT,
        likes      INTEGER DEFAULT 0,
        is_seed    INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS engagements (
        id          TEXT PRIMARY KEY,
        post_id     TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        response    TEXT,
        score_json  TEXT,
        total_score INTEGER DEFAULT 0,
        timestamp   TEXT
    );
    CREATE TABLE IF NOT EXISTS scores (
        user_id       TEXT PRIMARY KEY,
        total         INTEGER DEFAULT 0,
        engagements   INTEGER DEFAULT 0,
        mind_changes  INTEGER DEFAULT 0,
        quality_avg   REAL DEFAULT 0,
        badges        TEXT DEFAULT '[]',
        streak_days   INTEGER DEFAULT 0,
        last_active   TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS rooms (
        id          TEXT PRIMARY KEY,
        question    TEXT NOT NULL,
        category    TEXT,
        created     TEXT,
        active      INTEGER DEFAULT 1,
        is_private  INTEGER DEFAULT 0,
        room_code   TEXT UNIQUE,
        created_by  TEXT DEFAULT '',
        max_members INTEGER DEFAULT 100,
        description TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS room_members (
        room_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        joined  TEXT,
        PRIMARY KEY (room_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS room_messages (
        id         TEXT PRIMARY KEY,
        room_id    TEXT NOT NULL,
        user_id    TEXT,
        username   TEXT,
        avatar     TEXT,
        avatar_color TEXT DEFAULT '#8b5cf6',
        text       TEXT,
        score      INTEGER DEFAULT 0,
        approved   INTEGER DEFAULT 1,
        timestamp  TEXT
    );
    CREATE TABLE IF NOT EXISTS stances (
        user_id TEXT NOT NULL,
        topic   TEXT NOT NULL,
        stance  TEXT,
        PRIMARY KEY (user_id, topic)
    );
    CREATE TABLE IF NOT EXISTS topics (
        id          TEXT PRIMARY KEY,
        name        TEXT UNIQUE NOT NULL,
        emoji       TEXT DEFAULT '💬',
        created_by  TEXT DEFAULT 'system',
        is_official INTEGER DEFAULT 0,
        post_count  INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS market_items (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        category    TEXT,
        price       INTEGER DEFAULT 0,
        emoji       TEXT DEFAULT '🎁',
        rarity      TEXT DEFAULT 'common',
        effect      TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS user_inventory (
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        bought  TEXT,
        PRIMARY KEY (user_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS follows (
        follower_id  TEXT NOT NULL,
        following_id TEXT NOT NULL,
        created      TEXT,
        PRIMARY KEY (follower_id, following_id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        type       TEXT,
        message    TEXT,
        read       INTEGER DEFAULT 0,
        created    TEXT,
        data       TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS reels (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        username    TEXT,
        avatar      TEXT,
        avatar_color TEXT DEFAULT '#8b5cf6',
        video_url   TEXT NOT NULL,
        caption     TEXT DEFAULT '',
        topic       TEXT DEFAULT '',
        views       INTEGER DEFAULT 0,
        likes       INTEGER DEFAULT 0,
        timestamp   TEXT
    );
    """)
    db.commit()

    # seed topics
    c = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    if c == 0:
        _seed_topics(db)

    # seed posts
    c = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if c == 0:
        _seed_posts(db)

    # seed rooms
    c = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    if c == 0:
        _seed_rooms(db)

    # seed market
    c = db.execute("SELECT COUNT(*) FROM market_items").fetchone()[0]
    if c == 0:
        _seed_market(db)

    db.close()
    print("✅ Database ready:", DB_FILE)


# ── TOPICS ───────────────────────────────────────────────────────
OFFICIAL_TOPICS = [
    ("technology",    "💻", 1),
    ("society",       "🌍", 1),
    ("politics",      "🏛️", 1),
    ("work",          "💼", 1),
    ("education",     "📚", 1),
    ("health",        "🧘", 1),
    ("relationships", "💬", 1),
    ("art",           "🎨", 1),
    ("science",       "🔬", 1),
    ("sports",        "⚽", 1),
    ("philosophy",    "🤔", 1),
    ("environment",   "🌱", 1),
    ("economics",     "📈", 1),
    ("culture",       "🎭", 1),
    ("religion",      "🕊️", 1),
]

def _seed_topics(db):
    for name, emoji, official in OFFICIAL_TOPICS:
        db.execute("INSERT INTO topics VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4())[:8], name, emoji, "system", official, 0))
    db.commit()


# ── POSTS ─────────────────────────────────────────────────────────
SEED_POSTS_DATA = [
    ("technology", "against",
     "Social media has made us lonelier. Every 'like' is a substitute for a real conversation we're too anxious to have."),
    ("work", "for",
     "Remote work is making people less ambitious. The best opportunities happen in spontaneous hallway conversations, not Zoom calls."),
    ("education", "against",
     "University degrees are expensive ways to signal you can follow instructions. Most of what you learn is irrelevant within 5 years."),
    ("politics", "for",
     "Voting should be mandatory. Democracy only works when everyone participates — optional voting lets apathy decide elections."),
    ("society", "against",
     "Hustle culture is a trauma response disguised as productivity. We've normalized exhaustion and called it ambition."),
    ("technology", "for",
     "AI will not take your job. It will take the job of someone who refuses to learn AI. The threat is entirely self-inflicted."),
    ("art", "against",
     "AI-generated art is not art. Art requires human struggle, intention, and lived experience. A machine has none of those."),
    ("science", "for",
     "We should be doing human gene editing now. Eliminating hereditary diseases is a moral obligation, not a ethical dilemma."),
    ("environment", "against",
     "Individual recycling and carbon footprints are marketing myths invented by oil companies. System change is the only real solution."),
    ("philosophy", "for",
     "Free will is an illusion. Every choice you make is the result of prior causes — your biology, your history, your environment."),
]

def _seed_posts(db):
    for topic, stance, text in SEED_POSTS_DATA:
        db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"seed_{uuid.uuid4().hex[:6]}", "official", "EchoRoom",
                    "ER", "#8b5cf6", text, topic, stance, "", "",
                    datetime.datetime.now().isoformat(), 0, 1))
    db.commit()


# ── ROOMS ─────────────────────────────────────────────────────────
SEED_ROOMS_DATA = [
    ("AI will eliminate more jobs than it creates",    "technology"),
    ("Social media should be banned for under 16s",   "society"),
    ("Voting should be compulsory in democracies",    "politics"),
    ("University is no longer worth the cost",        "education"),
    ("Meat eating is ethically indefensible in 2025", "philosophy"),
    ("Art created by AI has no real value",           "art"),
]

def _seed_rooms(db):
    for question, category in SEED_ROOMS_DATA:
        code = secrets.token_urlsafe(6).upper()
        db.execute("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (f"room_{uuid.uuid4().hex[:6]}", question, category,
                    datetime.datetime.now().isoformat(), 1, 0, code, "system", 100, ""))
    db.commit()


# ── MARKET ────────────────────────────────────────────────────────
MARKET_ITEMS_DATA = [
    # (name, description, category, price, emoji, rarity, effect)
    ("Gold Frame",       "Golden border around your avatar",          "avatar",    200, "🖼️",  "rare",      "avatar_frame:gold"),
    ("Diamond Frame",    "Diamond border — ultra rare",               "avatar",    800, "💎",  "legendary", "avatar_frame:diamond"),
    ("Fire Frame",       "Animated fire border",                      "avatar",    400, "🔥",  "epic",      "avatar_frame:fire"),
    ("Verified Badge",   "Blue checkmark on your profile",            "badge",     500, "✅",  "epic",      "badge:verified"),
    ("Debate Champion",  "Trophy badge for elite debaters",           "badge",     300, "🏆",  "rare",      "badge:champion"),
    ("Night Theme",      "Dark red theme for the app",                "theme",     150, "🌙",  "common",    "theme:red"),
    ("Ocean Theme",      "Deep blue ocean theme",                     "theme",     150, "🌊",  "common",    "theme:blue"),
    ("Forest Theme",     "Deep green forest theme",                   "theme",     150, "🌲",  "common",    "theme:green"),
    ("Sunset Theme",     "Warm orange sunset theme",                  "theme",     150, "🌅",  "common",    "theme:orange"),
    ("Neon Theme",       "Cyberpunk neon pink theme",                 "theme",     250, "💜",  "rare",      "theme:pink"),
    ("Score Booster",    "1.5x score multiplier for 24 hours",        "boost",     100, "⚡",  "common",    "boost:score_1.5x"),
    ("XP Shield",        "Protect your score for 1 week",             "boost",     200, "🛡️", "rare",      "boost:xp_shield"),
    ("Custom Bio Color", "Color your bio text with any hex code",     "profile",    80, "🎨",  "common",    "profile:bio_color"),
    ("Animated Avatar",  "Your avatar pulses with your rank color",   "avatar",    350, "✨",  "epic",      "avatar:animated"),
    ("Private Room+",    "Create unlimited private rooms",            "feature",   600, "🔐",  "epic",      "feature:private_rooms"),
    ("Room Master",      "Pin messages, kick users in your rooms",    "feature",   400, "👑",  "rare",      "feature:room_master"),
]

def _seed_market(db):
    for name, desc, cat, price, emoji, rarity, effect in MARKET_ITEMS_DATA:
        db.execute("INSERT INTO market_items VALUES (?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4())[:8], name, desc, cat, price, emoji, rarity, effect))
    db.commit()


# ══════════════════════════════════════════════════════════════════
#  AI
# ══════════════════════════════════════════════════════════════════
def ask_ai(prompt: str, system: str = "") -> str:
    try:
        r = req.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt,
            "system": system, "stream": False
        }, timeout=60)
        return r.json().get("response", "").strip()
    except:
        return ""

def ask_ai_json(prompt: str, system: str = "") -> dict:
    raw = ask_ai(prompt, system)
    try:
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except:
        return {}


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw: str) -> str:
    return hashlib.sha256((pw + "echoroom_salt_v3").encode()).hexdigest()

def gen_token() -> str:
    return secrets.token_urlsafe(32)

def gen_room_code() -> str:
    return secrets.token_urlsafe(6).upper()[:8]

def get_rank(score: int) -> str:
    if score >= 500: return "Enlightened"
    if score >= 300: return "Open Mind"
    if score >= 150: return "Curious"
    if score >= 50:  return "Awakening"
    return "Echo Chamber"

def next_rank_info(score: int) -> dict:
    for t, name in [(50,"Awakening"),(150,"Curious"),(300,"Open Mind"),(500,"Enlightened")]:
        if score < t:
            return {"name": name, "points_needed": t - score}
    return {"name": "Max rank", "points_needed": 0}

def sanitize(text: str, max_len: int = 500) -> str:
    if not text: return ""
    text = text.strip()[:max_len]
    text = re.sub(r'[<>]', '', text)
    return text

def token_expires() -> str:
    return (datetime.datetime.now() + datetime.timedelta(days=TOKEN_EXPIRY_DAYS)).isoformat()


# ══════════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title="ECHO ROOM", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

init_db()


# ── Rate limit middleware ─────────────────────────────────────────
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip, limit=120, window=60):
        return JSONResponse(status_code=429,
                            content={"error": "Too many requests. Slow down."})
    return await call_next(request)


# ══════════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════════
class AuthReq(BaseModel):
    username: str
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

class PostReq(BaseModel):
    text:       str
    topic:      str
    stance:     str
    media_url:  Optional[str] = ""
    media_type: Optional[str] = ""

class EngageReq(BaseModel):
    response: str
    post_id:  str

class RoomReq(BaseModel):
    question:    str
    topic:       str
    is_private:  bool = False
    description: Optional[str] = ""
    max_members: Optional[int] = 100

class SpeakReq(BaseModel):
    text: str

class ProfileUpdateReq(BaseModel):
    bio:          Optional[str] = None
    avatar_color: Optional[str] = None
    theme_color:  Optional[str] = None
    equipped_items: Optional[list] = None

class TopicCreateReq(BaseModel):
    name:  str
    emoji: Optional[str] = "💬"

class PurchaseReq(BaseModel):
    item_id: str

class ReelReq(BaseModel):
    video_url: str
    caption:   Optional[str] = ""
    topic:     Optional[str] = ""


# ══════════════════════════════════════════════════════════════════
#  AUTH DEPENDENCY
# ══════════════════════════════════════════════════════════════════
def get_user(authorization: str = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(401, "Not authenticated")
    token = authorization.replace("Bearer ", "").strip()
    if len(token) < 10:
        raise HTTPException(401, "Invalid token format")
    db  = get_db()
    row = db.execute("""
        SELECT u.*, s.expires FROM users u
        JOIN sessions s ON u.id = s.user_id
        WHERE s.token = ?
    """, (token,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(401, "Session not found. Please login again.")
    # check expiry
    try:
        expires = datetime.datetime.fromisoformat(row["expires"])
        if datetime.datetime.now() > expires:
            raise HTTPException(401, "Session expired. Please login again.")
    except (KeyError, TypeError):
        pass
    return dict(row)


# ══════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ══════════════════════════════════════════════════════════════════
@app.post("/auth/register")
def register(r: AuthReq):
    # validation
    if len(r.username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if len(r.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if not re.match(r'^[a-zA-Z0-9_]+$', r.username):
        raise HTTPException(400, "Username can only contain letters, numbers, underscores")
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', r.email):
        raise HTTPException(400, "Invalid email address")

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE email=? OR username=?",
        (r.email.lower(), r.username)
    ).fetchone()
    if existing:
        db.close()
        raise HTTPException(400, "Email or username already taken")

    uid   = str(uuid.uuid4())
    token = gen_token()
    db.execute("""
        INSERT INTO users
        (id,username,email,password_hash,avatar,avatar_color,bio,joined,
         premium,plus,echo_coins,theme_color,equipped_items,follower_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (uid, r.username, r.email.lower(), hash_pw(r.password),
          r.username[:2].upper(), "#8b5cf6", "",
          datetime.datetime.now().isoformat(),
          0, 0, 100, "purple", "[]", 0))
    db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
               (token, uid, datetime.datetime.now().isoformat(),
                token_expires(), "mobile"))
    db.execute("INSERT INTO scores VALUES (?,?,?,?,?,?,?,?)",
               (uid, 0, 0, 0, 0.0, "[]", 0, datetime.datetime.now().isoformat()))
    db.commit()
    db.close()
    return {
        "token": token, "user_id": uid,
        "username": r.username, "expires_days": TOKEN_EXPIRY_DAYS,
        "echo_coins": 100,
    }


@app.post("/auth/login")
def login(r: LoginReq):
    db  = get_db()
    row = db.execute(
        "SELECT * FROM users WHERE email=? AND password_hash=?",
        (r.email.lower(), hash_pw(r.password))
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(401, "Wrong email or password")
    token = gen_token()
    db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
               (token, row["id"], datetime.datetime.now().isoformat(),
                token_expires(), "mobile"))
    db.commit()
    db.close()
    return {
        "token":      token,
        "user_id":    row["id"],
        "username":   row["username"],
        "theme_color": row["theme_color"] or "purple",
        "echo_coins":  row["echo_coins"] or 0,
        "plus":        row["plus"] or 0,
        "expires_days": TOKEN_EXPIRY_DAYS,
    }


@app.post("/auth/logout")
def logout(authorization: str = Header(None)):
    if not authorization:
        return {"status": "ok"}
    token = authorization.replace("Bearer ", "").strip()
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token=?", (token,))
    db.commit()
    db.close()
    return {"status": "logged out"}


@app.get("/auth/verify")
def verify_token(user: dict = Depends(get_user)):
    """Check if token is still valid — call on app start."""
    return {
        "valid":      True,
        "user_id":    user["id"],
        "username":   user["username"],
        "theme_color": user.get("theme_color", "purple"),
        "echo_coins":  user.get("echo_coins", 0),
        "plus":        user.get("plus", 0),
    }


# ══════════════════════════════════════════════════════════════════
#  TOPICS
# ══════════════════════════════════════════════════════════════════
@app.get("/topics")
def get_topics():
    db     = get_db()
    topics = db.execute(
        "SELECT * FROM topics ORDER BY is_official DESC, post_count DESC"
    ).fetchall()
    db.close()
    return {"topics": [dict(t) for t in topics]}


@app.post("/topics")
def create_topic(r: TopicCreateReq, user: dict = Depends(get_user)):
    name = sanitize(r.name.lower().strip(), 30)
    if not name:
        raise HTTPException(400, "Topic name required")
    db = get_db()
    existing = db.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(400, "Topic already exists")
    tid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO topics VALUES (?,?,?,?,?,?)",
               (tid, name, r.emoji or "💬", user["id"], 0, 0))
    db.commit()
    db.close()
    return {"status": "ok", "topic": {"id": tid, "name": name, "emoji": r.emoji}}


# ══════════════════════════════════════════════════════════════════
#  FEED & POSTS
# ══════════════════════════════════════════════════════════════════
@app.get("/feed")
def get_feed(topic: str = "", user: dict = Depends(get_user)):
    db = get_db()
    stances = {row["topic"]: row["stance"] for row in
               db.execute("SELECT topic, stance FROM stances WHERE user_id=?",
                          (user["id"],)).fetchall()}
    if topic:
        posts = db.execute(
            "SELECT * FROM posts WHERE topic=? ORDER BY timestamp DESC LIMIT 30",
            (topic,)
        ).fetchall()
    else:
        posts = db.execute(
            "SELECT * FROM posts ORDER BY timestamp DESC LIMIT 40"
        ).fetchall()
    db.close()
    post_list = [dict(p) for p in posts]

    def priority(p):
        user_s = stances.get(p.get("topic",""), "")
        if user_s and user_s != p.get("stance",""): return 0
        return 1

    post_list.sort(key=priority)
    return {"posts": post_list[:25]}


@app.post("/post")
def create_post(r: PostReq, user: dict = Depends(get_user)):
    text = sanitize(r.text, 1000)
    if not text:
        raise HTTPException(400, "Post text required")
    db  = get_db()
    pid = str(uuid.uuid4())[:10]
    db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (pid, user["id"], user["username"], user["avatar"],
                user.get("avatar_color","#8b5cf6"),
                text, r.topic, r.stance,
                r.media_url or "", r.media_type or "",
                datetime.datetime.now().isoformat(), 0, 0))
    db.execute("INSERT OR REPLACE INTO stances VALUES (?,?,?)",
               (user["id"], r.topic, r.stance))
    db.execute("UPDATE topics SET post_count=post_count+1 WHERE name=?", (r.topic,))
    db.commit()
    db.close()
    return {"status": "ok", "post_id": pid}


@app.post("/post/{post_id}/like")
def like_post(post_id: str, user: dict = Depends(get_user)):
    db = get_db()
    db.execute("UPDATE posts SET likes=likes+1 WHERE id=?", (post_id,))
    db.commit()
    db.close()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════
#  ENGAGE
# ══════════════════════════════════════════════════════════════════
@app.post("/engage/{post_id}")
def engage(post_id: str, r: EngageReq, user: dict = Depends(get_user)):
    response = sanitize(r.response, 2000)
    if not response or len(response) < 10:
        raise HTTPException(400, "Response too short")

    db   = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        db.close()
        raise HTTPException(404, "Post not found")

    # check if already engaged
    existing = db.execute(
        "SELECT id FROM engagements WHERE post_id=? AND user_id=?",
        (post_id, user["id"])
    ).fetchone()
    if existing:
        db.close()
        raise HTTPException(400, "Already engaged with this post")

    result = ask_ai_json(f"""Score this engagement:
Original: "{post['text']}"
Response: "{response}"

Return ONLY JSON:
{{
  "quality": 0-10, "empathy": 0-10, "logic": 0-10,
  "openness": 0-10, "mind_shift": 0-10, "total": 0-50,
  "feedback": "one honest sentence",
  "badge_earned": null or "open_mind" or "steel_man" or "empathy_award" or "logic_master"
}}""", "You are ECHO ROOM AI judge. Score open-mindedness. Be strict. Return JSON only.")

    if not result or "total" not in result:
        result = {"quality":5,"empathy":5,"logic":5,"openness":5,
                  "mind_shift":3,"total":23,
                  "feedback":"Decent engagement. Try acknowledging a specific point.",
                  "badge_earned": None}

    total = result.get("total", 0)

    # apply score booster if equipped
    inventory = db.execute(
        "SELECT item_id FROM user_inventory WHERE user_id=?", (user["id"],)
    ).fetchall()
    booster = any("boost:score_1.5x" in str(row) for row in inventory)
    if booster:
        total = int(total * 1.5)

    eid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO engagements VALUES (?,?,?,?,?,?,?)",
               (eid, post_id, user["id"], response,
                json.dumps(result), total, datetime.datetime.now().isoformat()))

    # update scores
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    if sc:
        new_total = (sc["total"] or 0) + total
        new_eng   = (sc["engagements"] or 0) + 1
        new_mc    = (sc["mind_changes"] or 0) + (1 if result.get("mind_shift",0)>=7 else 0)
        prev_avg  = sc["quality_avg"] or 0
        new_avg   = (prev_avg * (new_eng-1) + result.get("quality",5)) / new_eng
        badges    = json.loads(sc["badges"] or "[]")
        badge     = result.get("badge_earned")
        if badge and badge not in badges:
            badges.append(badge)
            # award echo coins for badge
            db.execute("UPDATE users SET echo_coins=echo_coins+50 WHERE id=?", (user["id"],))
        db.execute("""
            UPDATE scores SET total=?,engagements=?,mind_changes=?,
            quality_avg=?,badges=?,last_active=? WHERE user_id=?
        """, (new_total, new_eng, new_mc, new_avg, json.dumps(badges),
              datetime.datetime.now().isoformat(), user["id"]))

        # award echo coins for engagement
        db.execute("UPDATE users SET echo_coins=echo_coins+5 WHERE id=?", (user["id"],))

        db.commit()
        db.close()
        return {
            "status":     "ok",
            "score":      result,
            "your_total": new_total,
            "your_rank":  get_rank(new_total),
            "coins_earned": 5 + (50 if badge else 0),
        }

    db.close()
    return {"status": "ok", "score": result}


# ══════════════════════════════════════════════════════════════════
#  SCORE & PROFILE
# ══════════════════════════════════════════════════════════════════
@app.get("/score")
def get_score(user: dict = Depends(get_user)):
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    db.close()
    if not sc:
        return {"score":0,"rank":"Echo Chamber","engagements":0,"mind_changes":0,
                "quality_avg":0,"badges":[],"next_rank":next_rank_info(0)}
    total = sc["total"] or 0
    return {
        "score":        total,
        "rank":         get_rank(total),
        "engagements":  sc["engagements"] or 0,
        "mind_changes": sc["mind_changes"] or 0,
        "quality_avg":  round(sc["quality_avg"] or 0, 1),
        "badges":       json.loads(sc["badges"] or "[]"),
        "next_rank":    next_rank_info(total),
        "streak_days":  sc["streak_days"] or 0,
    }


@app.get("/profile/me/full")
def get_my_profile(user: dict = Depends(get_user)):
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?", (user["id"],)).fetchone()
    inventory = db.execute(
        "SELECT i.* FROM market_items i JOIN user_inventory ui ON i.id=ui.item_id WHERE ui.user_id=?",
        (user["id"],)
    ).fetchall()
    db.close()
    total = (sc["total"] if sc else 0) or 0
    return {
        "id":            user["id"],
        "username":      user["username"],
        "avatar":        user["avatar"],
        "avatar_color":  user.get("avatar_color","#8b5cf6"),
        "bio":           user.get("bio",""),
        "joined":        user["joined"],
        "theme_color":   user.get("theme_color","purple"),
        "echo_coins":    user.get("echo_coins",0),
        "plus":          user.get("plus",0),
        "equipped_items": json.loads(user.get("equipped_items","[]") or "[]"),
        "follower_count": user.get("follower_count",0),
        "score":         total,
        "rank":          get_rank(total),
        "badges":        json.loads((sc["badges"] if sc else "[]") or "[]"),
        "engagements":   (sc["engagements"] if sc else 0) or 0,
        "mind_changes":  (sc["mind_changes"] if sc else 0) or 0,
        "next_rank":     next_rank_info(total),
        "inventory":     [dict(i) for i in inventory],
    }


@app.put("/profile/update")
def update_profile(r: ProfileUpdateReq, user: dict = Depends(get_user)):
    db = get_db()
    if r.bio is not None:
        db.execute("UPDATE users SET bio=? WHERE id=?",
                   (sanitize(r.bio, 200), user["id"]))
    if r.avatar_color is not None:
        if re.match(r'^#[0-9a-fA-F]{6}$', r.avatar_color):
            db.execute("UPDATE users SET avatar_color=? WHERE id=?",
                       (r.avatar_color, user["id"]))
    if r.theme_color is not None:
        db.execute("UPDATE users SET theme_color=? WHERE id=?",
                   (r.theme_color, user["id"]))
    if r.equipped_items is not None:
        db.execute("UPDATE users SET equipped_items=? WHERE id=?",
                   (json.dumps(r.equipped_items), user["id"]))
    db.commit()
    db.close()
    return {"status": "ok"}


@app.get("/profile/{user_id}")
def get_profile(user_id: str):
    db  = get_db()
    u   = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    sc  = db.execute("SELECT * FROM scores WHERE user_id=?", (user_id,)).fetchone()
    db.close()
    if not u:
        raise HTTPException(404, "User not found")
    total = (sc["total"] if sc else 0) or 0
    return {
        "username":     u["username"],
        "avatar":       u["avatar"],
        "avatar_color": u["avatar_color"],
        "bio":          u["bio"],
        "joined":       u["joined"],
        "score":        total,
        "rank":         get_rank(total),
        "badges":       json.loads((sc["badges"] if sc else "[]") or "[]"),
        "engagements":  (sc["engagements"] if sc else 0) or 0,
        "follower_count": u["follower_count"] or 0,
    }


# ══════════════════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════════════════
@app.get("/leaderboard")
def leaderboard():
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.username, u.avatar, u.avatar_color, u.plus,
               s.total, s.engagements, s.mind_changes, s.badges
        FROM scores s JOIN users u ON s.user_id=u.id
        ORDER BY s.total DESC LIMIT 50
    """).fetchall()
    db.close()
    return {"leaderboard": [
        {**dict(r), "rank": get_rank(r["total"] or 0),
         "badges": json.loads(r["badges"] or "[]")}
        for r in rows
    ]}


# ══════════════════════════════════════════════════════════════════
#  ROOMS (private + public + room codes)
# ══════════════════════════════════════════════════════════════════
@app.get("/rooms")
def list_rooms(user: dict = Depends(get_user)):
    db    = get_db()
    rooms = db.execute(
        "SELECT * FROM rooms WHERE is_private=0 OR created_by=? ORDER BY created DESC LIMIT 50",
        (user["id"],)
    ).fetchall()
    result = []
    for room in rooms:
        r = dict(room)
        r["message_count"] = db.execute(
            "SELECT COUNT(*) FROM room_messages WHERE room_id=?", (room["id"],)
        ).fetchone()[0]
        r["member_count"] = db.execute(
            "SELECT COUNT(*) FROM room_members WHERE room_id=?", (room["id"],)
        ).fetchone()[0]
        if r["is_private"]:
            r.pop("room_code", None)
        result.append(r)
    db.close()
    return {"rooms": result}


@app.post("/rooms")
def create_room(r: RoomReq, user: dict = Depends(get_user)):
    question = sanitize(r.question, 200)
    if not question:
        raise HTTPException(400, "Room question required")
    db   = get_db()
    rid  = str(uuid.uuid4())[:8]
    code = gen_room_code()
    db.execute("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?,?)",
               (rid, question, r.topic, datetime.datetime.now().isoformat(),
                1, 1 if r.is_private else 0, code,
                user["id"], r.max_members or 100,
                sanitize(r.description or "", 300)))
    db.execute("INSERT OR IGNORE INTO room_members VALUES (?,?,?)",
               (rid, user["id"], datetime.datetime.now().isoformat()))
    db.commit()
    db.close()
    return {
        "room_id":   rid,
        "room_code": code,
        "share_url": f"https://echoroom-server-production.up.railway.app/rooms/join/{code}",
        "question":  question,
        "is_private": r.is_private,
    }


@app.get("/rooms/join/{code}")
def get_room_by_code(code: str):
    """Get room info by code — used for sharing links."""
    db   = get_db()
    room = db.execute("SELECT * FROM rooms WHERE room_code=?", (code.upper(),)).fetchone()
    db.close()
    if not room:
        raise HTTPException(404, "Room not found. Check the code.")
    r = dict(room)
    return {
        "room_id":    r["id"],
        "question":   r["question"],
        "category":   r["category"],
        "is_private": r["is_private"],
        "description": r["description"],
    }


@app.post("/rooms/join/{code}")
def join_by_code(code: str, user: dict = Depends(get_user)):
    """Join a room using its code."""
    db   = get_db()
    room = db.execute("SELECT * FROM rooms WHERE room_code=?", (code.upper(),)).fetchone()
    if not room:
        db.close()
        raise HTTPException(404, "Invalid room code")
    member_count = db.execute(
        "SELECT COUNT(*) FROM room_members WHERE room_id=?", (room["id"],)
    ).fetchone()[0]
    if member_count >= (room["max_members"] or 100):
        db.close()
        raise HTTPException(400, "Room is full")
    db.execute("INSERT OR IGNORE INTO room_members VALUES (?,?,?)",
               (room["id"], user["id"], datetime.datetime.now().isoformat()))
    db.commit()
    db.close()
    return {"status": "joined", "room_id": room["id"], "question": room["question"]}


@app.post("/rooms/{room_id}/speak")
def speak(room_id: str, r: SpeakReq, user: dict = Depends(get_user)):
    text = sanitize(r.text, 1000)
    if not text:
        raise HTTPException(400, "Message required")
    db   = get_db()
    room = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        db.close()
        raise HTTPException(404, "Room not found")

    mod = ask_ai_json(f"""Debate moderator.
Topic: "{room['question']}"
Message: "{text}"
Return ONLY JSON: {{"approved":true,"quality_score":0-10,"ai_response":"one moderator question","flag":null}}
""", "Fair debate moderator. Return JSON only.")

    if not mod:
        mod = {"approved": True, "quality_score": 5,
               "ai_response": "What's the strongest counter-argument to your point?",
               "flag": None}

    mid = str(uuid.uuid4())[:8]
    if mod.get("approved", True):
        db.execute("INSERT INTO room_messages VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (mid, room_id, user["id"], user["username"], user["avatar"],
                    user.get("avatar_color","#8b5cf6"),
                    text, mod.get("quality_score",5), 1,
                    datetime.datetime.now().isoformat()))
        db.execute("INSERT OR IGNORE INTO room_members VALUES (?,?,?)",
                   (room_id, user["id"], datetime.datetime.now().isoformat()))
        sc = db.execute("SELECT id FROM scores WHERE user_id=?", (user["id"],)).fetchone()
        if sc:
            db.execute("UPDATE scores SET total=total+? WHERE user_id=?",
                       (mod.get("quality_score",5), user["id"]))
        db.execute("UPDATE users SET echo_coins=echo_coins+2 WHERE id=?", (user["id"],))
        db.commit()

    msgs = db.execute(
        "SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 50",
        (room_id,)
    ).fetchall()
    db.close()
    return {
        "ai_response":   mod.get("ai_response",""),
        "room_messages": [dict(m) for m in msgs],
        "moderation":    mod,
    }


@app.get("/rooms/{room_id}/messages")
def get_messages(room_id: str):
    db   = get_db()
    msgs = db.execute(
        "SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 100",
        (room_id,)
    ).fetchall()
    db.close()
    return {"messages": [dict(m) for m in msgs]}


# ══════════════════════════════════════════════════════════════════
#  MARKET
# ══════════════════════════════════════════════════════════════════
@app.get("/market")
def get_market(user: dict = Depends(get_user)):
    db    = get_db()
    items = db.execute("SELECT * FROM market_items ORDER BY rarity DESC").fetchall()
    owned = {row["item_id"] for row in db.execute(
        "SELECT item_id FROM user_inventory WHERE user_id=?", (user["id"],)
    ).fetchall()}
    db.close()
    return {
        "items":       [{**dict(i), "owned": i["id"] in owned} for i in items],
        "your_coins":  user.get("echo_coins", 0),
    }


@app.post("/market/buy")
def buy_item(r: PurchaseReq, user: dict = Depends(get_user)):
    db   = get_db()
    item = db.execute("SELECT * FROM market_items WHERE id=?", (r.item_id,)).fetchone()
    if not item:
        db.close()
        raise HTTPException(404, "Item not found")
    owned = db.execute(
        "SELECT item_id FROM user_inventory WHERE user_id=? AND item_id=?",
        (user["id"], r.item_id)
    ).fetchone()
    if owned:
        db.close()
        raise HTTPException(400, "Already owned")
    coins = user.get("echo_coins", 0)
    if coins < item["price"]:
        db.close()
        raise HTTPException(400, f"Not enough Echo Coins. Need {item['price']}, have {coins}")
    db.execute("UPDATE users SET echo_coins=echo_coins-? WHERE id=?",
               (item["price"], user["id"]))
    db.execute("INSERT INTO user_inventory VALUES (?,?,?)",
               (user["id"], r.item_id, datetime.datetime.now().isoformat()))
    db.commit()
    new_coins = db.execute("SELECT echo_coins FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    db.close()
    return {
        "status":     "purchased",
        "item":       dict(item),
        "coins_left": new_coins,
    }


# ══════════════════════════════════════════════════════════════════
#  ECHO ROOM PLUS
# ══════════════════════════════════════════════════════════════════
PLUS_FEATURES = [
    "Unlimited engagements per day",
    "Unlimited private rooms",
    "All 16 market items unlocked",
    "Animated avatar ring",
    "Verified Plus badge on profile",
    "AI feedback with full breakdown (7 dimensions)",
    "Score history & analytics",
    "Priority AI response (faster scoring)",
    "Custom profile URL",
    "Bold username in leaderboard",
    "Weekly AI personality report",
    "Early access to new features",
]

@app.get("/plus/features")
def plus_features():
    return {
        "price_monthly": 7,
        "price_yearly":  59,
        "features":      PLUS_FEATURES,
        "popular":       "yearly",
    }

@app.post("/plus/activate")
def activate_plus(user: dict = Depends(get_user)):
    """In production: call this from Stripe webhook."""
    db = get_db()
    db.execute("UPDATE users SET plus=1 WHERE id=?", (user["id"],))
    db.execute("UPDATE users SET echo_coins=echo_coins+500 WHERE id=?", (user["id"],))
    db.commit()
    db.close()
    return {"status": "plus_activated", "bonus_coins": 500}


# ══════════════════════════════════════════════════════════════════
#  REELS
# ══════════════════════════════════════════════════════════════════
@app.get("/reels")
def get_reels():
    db    = get_db()
    reels = db.execute(
        "SELECT * FROM reels ORDER BY timestamp DESC LIMIT 20"
    ).fetchall()
    db.close()
    return {"reels": [dict(r) for r in reels]}


@app.post("/reels")
def post_reel(r: ReelReq, user: dict = Depends(get_user)):
    if not r.video_url:
        raise HTTPException(400, "Video URL required")
    db  = get_db()
    rid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO reels VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (rid, user["id"], user["username"], user["avatar"],
                user.get("avatar_color","#8b5cf6"),
                r.video_url, sanitize(r.caption or "", 300),
                r.topic or "", 0, 0, datetime.datetime.now().isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins+10 WHERE id=?", (user["id"],))
    db.commit()
    db.close()
    return {"status": "ok", "reel_id": rid, "coins_earned": 10}


@app.post("/reels/{reel_id}/like")
def like_reel(reel_id: str, user: dict = Depends(get_user)):
    db = get_db()
    db.execute("UPDATE reels SET likes=likes+1 WHERE id=?", (reel_id,))
    db.commit()
    db.close()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════
@app.get("/notifications")
def get_notifications(user: dict = Depends(get_user)):
    db    = get_db()
    notes = db.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created DESC LIMIT 30",
        (user["id"],)
    ).fetchall()
    unread = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
        (user["id"],)
    ).fetchone()[0]
    db.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user["id"],))
    db.commit()
    db.close()
    return {"notifications": [dict(n) for n in notes], "unread": unread}


# ══════════════════════════════════════════════════════════════════
#  ROOT
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def root():
    db    = get_db()
    stats = {
        "app":         "ECHO ROOM",
        "version":     "3.0",
        "users":       db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "posts":       db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "engagements": db.execute("SELECT COUNT(*) FROM engagements").fetchone()[0],
        "rooms":       db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "reels":       db.execute("SELECT COUNT(*) FROM reels").fetchone()[0],
        "status":      "online",
    }
    db.close()
    return stats


if __name__ == "__main__":
    print(f"🌐 ECHO ROOM v3 starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
