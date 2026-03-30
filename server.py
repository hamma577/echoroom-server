import os, json, uuid, datetime, re, sqlite3, hashlib, secrets, time
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

PORT    = int(os.getenv("PORT", 8000))
DB_FILE = os.getenv("DB_FILE", "echoroom_v3.db")

app = FastAPI(title="ECHO ROOM", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

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
        id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        avatar TEXT, avatar_color TEXT DEFAULT '#8b5cf6',
        bio TEXT DEFAULT '', joined TEXT,
        premium INTEGER DEFAULT 0, plus INTEGER DEFAULT 0,
        echo_coins INTEGER DEFAULT 100, theme_color TEXT DEFAULT 'purple',
        equipped_items TEXT DEFAULT '[]', follower_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        created TEXT, expires TEXT
    );
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        username TEXT, avatar TEXT, avatar_color TEXT DEFAULT '#8b5cf6',
        text TEXT, topic TEXT, stance TEXT,
        timestamp TEXT, likes INTEGER DEFAULT 0, is_seed INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS engagements (
        id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
        user_id TEXT NOT NULL, response TEXT,
        score_json TEXT, total_score INTEGER DEFAULT 0, timestamp TEXT
    );
    CREATE TABLE IF NOT EXISTS scores (
        user_id TEXT PRIMARY KEY, total INTEGER DEFAULT 0,
        engagements INTEGER DEFAULT 0, mind_changes INTEGER DEFAULT 0,
        quality_avg REAL DEFAULT 0, badges TEXT DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS rooms (
        id TEXT PRIMARY KEY, question TEXT NOT NULL,
        category TEXT, created TEXT, active INTEGER DEFAULT 1,
        is_private INTEGER DEFAULT 0, room_code TEXT,
        created_by TEXT DEFAULT '', description TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS room_messages (
        id TEXT PRIMARY KEY, room_id TEXT NOT NULL,
        user_id TEXT, username TEXT, avatar TEXT,
        avatar_color TEXT DEFAULT '#8b5cf6',
        text TEXT, score INTEGER DEFAULT 0,
        approved INTEGER DEFAULT 1, timestamp TEXT
    );
    CREATE TABLE IF NOT EXISTS topics (
        id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL,
        emoji TEXT DEFAULT '💬', created_by TEXT DEFAULT 'system',
        is_official INTEGER DEFAULT 0, post_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS market_items (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        description TEXT, category TEXT,
        price INTEGER DEFAULT 0, emoji TEXT DEFAULT '🎁',
        rarity TEXT DEFAULT 'common', effect TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS user_inventory (
        user_id TEXT NOT NULL, item_id TEXT NOT NULL,
        bought TEXT, PRIMARY KEY (user_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS reels (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        username TEXT, avatar TEXT, video_url TEXT NOT NULL,
        caption TEXT DEFAULT '', topic TEXT DEFAULT '',
        views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, timestamp TEXT
    );
    CREATE TABLE IF NOT EXISTS streaks (
        user_id TEXT PRIMARY KEY,
        current INTEGER DEFAULT 0, longest INTEGER DEFAULT 0,
        last_active TEXT DEFAULT '', shield_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS battles (
        id TEXT PRIMARY KEY, challenger TEXT NOT NULL,
        challenged TEXT NOT NULL, topic TEXT,
        status TEXT DEFAULT 'pending', rounds_json TEXT DEFAULT '[]',
        winner TEXT DEFAULT '', created TEXT, expires TEXT
    );
    CREATE TABLE IF NOT EXISTS parliament_votes (
        id TEXT PRIMARY KEY, question TEXT NOT NULL,
        option_a TEXT, option_b TEXT,
        active INTEGER DEFAULT 1, created TEXT, ends TEXT
    );
    CREATE TABLE IF NOT EXISTS parliament_responses (
        vote_id TEXT NOT NULL, user_id TEXT NOT NULL,
        choice TEXT, country TEXT DEFAULT '', created TEXT,
        PRIMARY KEY (vote_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS dna_reports (
        user_id TEXT PRIMARY KEY, report_json TEXT, generated TEXT
    );
    """)
    db.commit()

    if db.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 0:
        for name, emoji in [
            ("technology","💻"),("society","🌍"),("politics","🏛️"),
            ("work","💼"),("education","📚"),("health","🧘"),
            ("relationships","💬"),("art","🎨"),("science","🔬"),
            ("sports","⚽"),("philosophy","🤔"),("environment","🌱"),
            ("economics","📈"),("culture","🎭"),("religion","🕊️")
        ]:
            db.execute("INSERT INTO topics VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4())[:8], name, emoji, "system", 1, 0))
        db.commit()

    if db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0:
        for topic, stance, text in [
            ("technology","against","Social media has made us lonelier. Every like is a substitute for real conversation."),
            ("work","for","Remote work is making people less ambitious. The best opportunities happen in person."),
            ("education","against","University degrees are expensive signals. Most of what you learn is irrelevant within 5 years."),
            ("politics","for","Voting should be mandatory. Democracy only works when everyone participates."),
            ("society","against","Hustle culture is a trauma response disguised as productivity."),
            ("technology","for","AI will not take your job. It will take the job of someone who refuses to learn AI."),
            ("art","against","AI-generated art is not real art. Art requires human struggle and lived experience."),
            ("philosophy","for","Free will is an illusion. Every choice is the result of prior causes."),
            ("environment","against","Individual recycling is a myth invented by oil companies. System change is the only solution."),
            ("science","for","We should be doing human gene editing now. Eliminating hereditary diseases is a moral obligation."),
        ]:
            db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"seed_{uuid.uuid4().hex[:6]}", "official", "EchoRoom",
                 "ER", "#8b5cf6", text, topic, stance,
                 datetime.datetime.now().isoformat(), 0, 1))
        db.commit()

    if db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
        for question, cat in [
            ("AI will eliminate more jobs than it creates", "technology"),
            ("Social media should be banned for under 16s", "society"),
            ("Voting should be compulsory in democracies", "politics"),
            ("University is no longer worth the cost", "education"),
            ("Art created by AI has no real value", "art"),
        ]:
            code = secrets.token_urlsafe(6).upper()[:8]
            db.execute("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
                (f"room_{uuid.uuid4().hex[:6]}", question, cat,
                 datetime.datetime.now().isoformat(), 1, 0, code, "system", ""))
        db.commit()

    if db.execute("SELECT COUNT(*) FROM market_items").fetchone()[0] == 0:
        for name, desc, cat, price, emoji, rarity, effect in [
            ("Gold Frame",    "Golden border around your avatar",   "avatar",  200, "🖼️",  "rare",      "avatar_frame:gold"),
            ("Diamond Frame", "Diamond border — ultra rare",        "avatar",  800, "💎",  "legendary", "avatar_frame:diamond"),
            ("Fire Frame",    "Animated fire border",               "avatar",  400, "🔥",  "epic",      "avatar_frame:fire"),
            ("Verified Badge","Blue checkmark on your profile",     "badge",   500, "✅",  "epic",      "badge:verified"),
            ("Night Theme",   "Dark red theme",                     "theme",   150, "🌙",  "common",    "theme:red"),
            ("Ocean Theme",   "Deep blue theme",                    "theme",   150, "🌊",  "common",    "theme:blue"),
            ("Forest Theme",  "Deep green theme",                   "theme",   150, "🌲",  "common",    "theme:green"),
            ("Neon Theme",    "Cyberpunk pink theme",               "theme",   250, "💜",  "rare",      "theme:pink"),
            ("Score Booster", "1.5x score multiplier for 24h",      "boost",   100, "⚡",  "common",    "boost:score"),
            ("XP Shield",     "Protect your streak for 1 week",     "boost",   200, "🛡️", "rare",      "boost:shield"),
            ("Custom Bio",    "Color your bio text",                "profile",  80, "🎨",  "common",    "profile:bio"),
            ("Room Master",   "Pin messages in your rooms",         "feature", 400, "👑",  "rare",      "feature:room"),
        ]:
            db.execute("INSERT INTO market_items VALUES (?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4())[:8], name, desc, cat, price, emoji, rarity, effect))
        db.commit()

    if db.execute("SELECT COUNT(*) FROM parliament_votes").fetchone()[0] == 0:
        db.execute("INSERT INTO parliament_votes VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4())[:8],
             "Should social media companies be held legally responsible for mental health damage?",
             "Yes, they should", "No, users choose freely",
             1, datetime.datetime.now().isoformat(),
             (datetime.datetime.now() + datetime.timedelta(days=7)).isoformat()))
        db.commit()

    db.close()
    print(f"✅ Database ready: {DB_FILE}")

init_db()

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw): return hashlib.sha256((pw + "echoroom_salt_v3").encode()).hexdigest()
def sanitize(t, n=500): return re.sub(r'[<>]', '', str(t or '').strip())[:n]
def get_rank(s):
    if s>=500: return "Enlightened"
    if s>=300: return "Open Mind"
    if s>=150: return "Curious"
    if s>=50:  return "Awakening"
    return "Echo Chamber"
def next_rank_info(s):
    for t,n in [(50,"Awakening"),(150,"Curious"),(300,"Open Mind"),(500,"Enlightened")]:
        if s<t: return {"name":n,"points_needed":t-s}
    return {"name":"Max rank","points_needed":0}
def token_expires():
    return (datetime.datetime.now()+datetime.timedelta(days=30)).isoformat()

def get_user(authorization: str = Header(None)):
    if not authorization: raise HTTPException(401,"Not authenticated")
    token = authorization.replace("Bearer ","").strip()
    db  = get_db()
    row = db.execute("""SELECT u.* FROM users u
        JOIN sessions s ON u.id=s.user_id WHERE s.token=?""",(token,)).fetchone()
    db.close()
    if not row: raise HTTPException(401,"Session expired. Please login again.")
    return dict(row)

# ══════════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════════
class AuthReq(BaseModel):
    username: str; email: str; password: str
class LoginReq(BaseModel):
    email: str; password: str
class PostReq(BaseModel):
    text: str; topic: str; stance: str
class EngageReq(BaseModel):
    response: str; post_id: str
class RoomReq(BaseModel):
    question: str; topic: str; is_private: bool=False; description: Optional[str]=""
class SpeakReq(BaseModel):
    text: str
class ProfileUpdateReq(BaseModel):
    bio: Optional[str]=None; avatar_color: Optional[str]=None; theme_color: Optional[str]=None
class PurchaseReq(BaseModel):
    item_id: str
class ReelReq(BaseModel):
    video_url: str; caption: Optional[str]=""; topic: Optional[str]=""
class TopicReq(BaseModel):
    name: str; emoji: Optional[str]="💬"
class BattleReq(BaseModel):
    challenged_id: str; topic: str
class BattleArgReq(BaseModel):
    argument: str
class ParliamentVoteReq(BaseModel):
    vote_id: str; choice: str; country: Optional[str]=""

# ══════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════
@app.post("/auth/register")
def register(r: AuthReq):
    if len(r.username)<3: raise HTTPException(400,"Username must be at least 3 characters")
    if len(r.password)<6: raise HTTPException(400,"Password must be at least 6 characters")
    if not re.match(r'^[a-zA-Z0-9_]+$',r.username):
        raise HTTPException(400,"Username: letters, numbers, underscores only")
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email=? OR username=?",
                  (r.email.lower(),r.username)).fetchone():
        db.close(); raise HTTPException(400,"Email or username already taken")
    uid   = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    db.execute("""INSERT INTO users
        (id,username,email,password_hash,avatar,avatar_color,bio,joined,
         premium,plus,echo_coins,theme_color,equipped_items,follower_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid,r.username,r.email.lower(),hash_pw(r.password),
         r.username[:2].upper(),"#8b5cf6","",
         datetime.datetime.now().isoformat(),0,0,100,"purple","[]",0))
    db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
               (token,uid,datetime.datetime.now().isoformat(),token_expires()))
    db.execute("INSERT INTO scores VALUES (?,?,?,?,?,?)",(uid,0,0,0,0.0,"[]"))
    db.execute("INSERT INTO streaks VALUES (?,?,?,?,?)",(uid,0,0,"",0))
    db.commit(); db.close()
    return {"token":token,"user_id":uid,"username":r.username,
            "echo_coins":100,"plus":0,"theme_color":"purple","avatar_color":"#8b5cf6"}

@app.post("/auth/login")
def login(r: LoginReq):
    db  = get_db()
    row = db.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                     (r.email.lower(),hash_pw(r.password))).fetchone()
    if not row: db.close(); raise HTTPException(401,"Wrong email or password")
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
               (token,row["id"],datetime.datetime.now().isoformat(),token_expires()))
    db.commit(); db.close()
    return {"token":token,"user_id":row["id"],"username":row["username"],
            "echo_coins":row["echo_coins"] or 0,"plus":row["plus"] or 0,
            "theme_color":row["theme_color"] or "purple",
            "avatar_color":row["avatar_color"] or "#8b5cf6"}

@app.post("/auth/logout")
def logout(authorization: str=Header(None)):
    if authorization:
        token = authorization.replace("Bearer ","").strip()
        db=get_db(); db.execute("DELETE FROM sessions WHERE token=?",(token,))
        db.commit(); db.close()
    return {"status":"logged out"}

@app.get("/auth/verify")
def verify(user: dict=Depends(get_user)):
    return {"valid":True,"user_id":user["id"],"username":user["username"],
            "theme_color":user.get("theme_color","purple"),
            "echo_coins":user.get("echo_coins",0),"plus":user.get("plus",0),
            "avatar_color":user.get("avatar_color","#8b5cf6")}

# ══════════════════════════════════════════════════════════════════
#  TOPICS
# ══════════════════════════════════════════════════════════════════
@app.get("/topics")
def get_topics():
    db=get_db(); rows=db.execute("SELECT * FROM topics ORDER BY is_official DESC,post_count DESC").fetchall(); db.close()
    return {"topics":[dict(r) for r in rows]}

@app.post("/topics")
def create_topic(r: TopicReq, user: dict=Depends(get_user)):
    name=sanitize(r.name.lower().strip(),30)
    db=get_db()
    if db.execute("SELECT id FROM topics WHERE name=?",(name,)).fetchone():
        db.close(); raise HTTPException(400,"Topic already exists")
    tid=str(uuid.uuid4())[:8]
    db.execute("INSERT INTO topics VALUES (?,?,?,?,?,?)",(tid,name,r.emoji or "💬",user["id"],0,0))
    db.commit(); db.close()
    return {"status":"ok","topic":{"id":tid,"name":name,"emoji":r.emoji}}

# ══════════════════════════════════════════════════════════════════
#  FEED & POSTS
# ══════════════════════════════════════════════════════════════════
@app.get("/feed")
def get_feed(topic: str="", user: dict=Depends(get_user)):
    db=get_db()
    if topic:
        posts=db.execute("SELECT * FROM posts WHERE topic=? ORDER BY timestamp DESC LIMIT 30",(topic,)).fetchall()
    else:
        posts=db.execute("SELECT * FROM posts ORDER BY timestamp DESC LIMIT 40").fetchall()
    db.close()
    return {"posts":[dict(p) for p in posts]}

@app.post("/post")
def create_post(r: PostReq, user: dict=Depends(get_user)):
    text=sanitize(r.text,1000)
    if not text: raise HTTPException(400,"Post text required")
    db=get_db(); pid=str(uuid.uuid4())[:10]
    db.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (pid,user["id"],user["username"],user["avatar"],
                user.get("avatar_color","#8b5cf6"),text,r.topic,r.stance,
                datetime.datetime.now().isoformat(),0,0))
    db.execute("UPDATE topics SET post_count=post_count+1 WHERE name=?",(r.topic,))
    db.commit(); db.close()
    return {"status":"ok","post_id":pid}

@app.post("/post/{post_id}/like")
def like_post(post_id: str, user: dict=Depends(get_user)):
    db=get_db(); db.execute("UPDATE posts SET likes=likes+1 WHERE id=?",(post_id,)); db.commit(); db.close()
    return {"status":"ok"}

# ══════════════════════════════════════════════════════════════════
#  STREAK
# ══════════════════════════════════════════════════════════════════
def _update_streak(user_id, db):
    today=datetime.date.today().isoformat()
    row=db.execute("SELECT * FROM streaks WHERE user_id=?",(user_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO streaks VALUES (?,?,?,?,?)",(user_id,1,1,today,0)); return 1
    last=( row["last_active"] or "")[:10]
    if last==today: return row["current"] or 0
    yesterday=str(datetime.date.today()-datetime.timedelta(days=1))
    current=row["current"] or 0
    if last==yesterday: current+=1
    else:
        if (row["shield_count"] or 0)>0:
            db.execute("UPDATE streaks SET shield_count=shield_count-1 WHERE user_id=?",(user_id,))
            current+=1
        else: current=1
    longest=max(row["longest"] or 0,current)
    coins=10 if current%7==0 else 3
    db.execute("UPDATE users SET echo_coins=echo_coins+? WHERE id=?",(coins,user_id))
    db.execute("UPDATE streaks SET current=?,longest=?,last_active=? WHERE user_id=?",(current,longest,today,user_id))
    return current

@app.get("/streak")
def get_streak(user: dict=Depends(get_user)):
    db=get_db(); row=db.execute("SELECT * FROM streaks WHERE user_id=?",(user["id"],)).fetchone(); db.close()
    if not row: return {"current":0,"longest":0,"shield_count":0,"status":"start"}
    today=datetime.date.today().isoformat(); last=(row["last_active"] or "")[:10]
    current=row["current"] or 0
    if last==today: status="done_today"
    elif last==str(datetime.date.today()-datetime.timedelta(days=1)): status="active"
    else: status="broken"; current=0
    return {"current":current,"longest":row["longest"] or 0,
            "shield_count":row["shield_count"] or 0,"status":status}

# ══════════════════════════════════════════════════════════════════
#  ENGAGE
# ══════════════════════════════════════════════════════════════════
@app.post("/engage/{post_id}")
def engage(post_id: str, r: EngageReq, user: dict=Depends(get_user)):
    response=sanitize(r.response,2000)
    if len(response)<10: raise HTTPException(400,"Response too short")
    db=get_db()
    post=db.execute("SELECT * FROM posts WHERE id=?",(post_id,)).fetchone()
    if not post: db.close(); raise HTTPException(404,"Post not found")
    if db.execute("SELECT id FROM engagements WHERE post_id=? AND user_id=?",(post_id,user["id"])).fetchone():
        db.close(); raise HTTPException(400,"Already engaged with this post")
    total=25
    result={"quality":5,"empathy":5,"logic":5,"openness":5,"mind_shift":5,
            "total":total,"feedback":"Good engagement. Keep exploring different perspectives.","badge_earned":None}
    eid=str(uuid.uuid4())[:8]
    db.execute("INSERT INTO engagements VALUES (?,?,?,?,?,?,?)",
               (eid,post_id,user["id"],response,json.dumps(result),total,datetime.datetime.now().isoformat()))
    sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user["id"],)).fetchone()
    if sc:
        new_total=(sc["total"] or 0)+total; new_eng=(sc["engagements"] or 0)+1
        new_avg=((sc["quality_avg"] or 0)*(new_eng-1)+5)/new_eng
        db.execute("UPDATE scores SET total=?,engagements=?,quality_avg=? WHERE user_id=?",
                   (new_total,new_eng,new_avg,user["id"]))
    db.execute("UPDATE users SET echo_coins=echo_coins+5 WHERE id=?",(user["id"],))
    _update_streak(user["id"],db)
    db.commit(); db.close()
    return {"status":"ok","score":result,"coins_earned":5}

# ══════════════════════════════════════════════════════════════════
#  SCORE & PROFILE
# ══════════════════════════════════════════════════════════════════
@app.get("/score")
def get_score(user: dict=Depends(get_user)):
    db=get_db(); sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user["id"],)).fetchone(); db.close()
    total=(sc["total"] if sc else 0) or 0
    return {"score":total,"rank":get_rank(total),
            "engagements":(sc["engagements"] if sc else 0) or 0,
            "badges":json.loads((sc["badges"] if sc else "[]") or "[]"),
            "next_rank":next_rank_info(total)}

@app.get("/profile/me/full")
def my_profile(user: dict=Depends(get_user)):
    db=get_db()
    sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user["id"],)).fetchone()
    streak=db.execute("SELECT * FROM streaks WHERE user_id=?",(user["id"],)).fetchone()
    db.close()
    total=(sc["total"] if sc else 0) or 0
    return {"id":user["id"],"username":user["username"],"avatar":user["avatar"],
            "avatar_color":user.get("avatar_color","#8b5cf6"),"bio":user.get("bio",""),
            "joined":user["joined"],"theme_color":user.get("theme_color","purple"),
            "echo_coins":user.get("echo_coins",0),"plus":user.get("plus",0),
            "equipped_items":json.loads(user.get("equipped_items","[]") or "[]"),
            "score":total,"rank":get_rank(total),
            "badges":json.loads((sc["badges"] if sc else "[]") or "[]"),
            "engagements":(sc["engagements"] if sc else 0) or 0,
            "mind_changes":(sc["mind_changes"] if sc else 0) or 0,
            "next_rank":next_rank_info(total),
            "streak":(streak["current"] if streak else 0) or 0}

@app.put("/profile/update")
def update_profile(r: ProfileUpdateReq, user: dict=Depends(get_user)):
    db=get_db()
    if r.bio is not None: db.execute("UPDATE users SET bio=? WHERE id=?",(sanitize(r.bio,200),user["id"]))
    if r.avatar_color and re.match(r'^#[0-9a-fA-F]{6}$',r.avatar_color):
        db.execute("UPDATE users SET avatar_color=? WHERE id=?",(r.avatar_color,user["id"]))
    if r.theme_color: db.execute("UPDATE users SET theme_color=? WHERE id=?",(r.theme_color,user["id"]))
    db.commit(); db.close()
    return {"status":"ok"}

@app.get("/profile/{user_id}")
def get_profile(user_id: str):
    db=get_db(); u=db.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
    sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user_id,)).fetchone(); db.close()
    if not u: raise HTTPException(404,"User not found")
    total=(sc["total"] if sc else 0) or 0
    return {"username":u["username"],"avatar":u["avatar"],"avatar_color":u["avatar_color"],
            "bio":u["bio"],"score":total,"rank":get_rank(total)}

# ══════════════════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════════════════
@app.get("/leaderboard")
def leaderboard():
    db=get_db()
    rows=db.execute("""SELECT u.id,u.username,u.avatar,u.avatar_color,u.plus,
        s.total,s.engagements,s.badges FROM scores s
        JOIN users u ON s.user_id=u.id ORDER BY s.total DESC LIMIT 50""").fetchall()
    db.close()
    return {"leaderboard":[{**dict(r),"rank":get_rank(r["total"] or 0),
            "badges":json.loads(r["badges"] or "[]")} for r in rows]}

# ══════════════════════════════════════════════════════════════════
#  ROOMS
# ══════════════════════════════════════════════════════════════════
@app.get("/rooms")
def list_rooms(user: dict=Depends(get_user)):
    db=get_db()
    rooms=db.execute("SELECT * FROM rooms WHERE is_private=0 OR created_by=? ORDER BY created DESC LIMIT 50",(user["id"],)).fetchall()
    result=[]
    for room in rooms:
        r=dict(room)
        r["message_count"]=db.execute("SELECT COUNT(*) FROM room_messages WHERE room_id=?",(room["id"],)).fetchone()[0]
        result.append(r)
    db.close()
    return {"rooms":result}

@app.post("/rooms")
def create_room(r: RoomReq, user: dict=Depends(get_user)):
    question=sanitize(r.question,200)
    if not question: raise HTTPException(400,"Room question required")
    db=get_db(); rid=str(uuid.uuid4())[:8]; code=secrets.token_urlsafe(6).upper()[:8]
    db.execute("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
               (rid,question,r.topic,datetime.datetime.now().isoformat(),
                1,1 if r.is_private else 0,code,user["id"],sanitize(r.description or "",300)))
    db.commit(); db.close()
    return {"room_id":rid,"room_code":code,"question":question,"is_private":r.is_private}

@app.get("/rooms/join/{code}")
def get_room_by_code(code: str):
    db=get_db(); room=db.execute("SELECT * FROM rooms WHERE room_code=?",(code.upper(),)).fetchone(); db.close()
    if not room: raise HTTPException(404,"Room not found")
    return {"room_id":room["id"],"question":room["question"],"category":room["category"]}

@app.post("/rooms/join/{code}")
def join_by_code(code: str, user: dict=Depends(get_user)):
    db=get_db(); room=db.execute("SELECT * FROM rooms WHERE room_code=?",(code.upper(),)).fetchone()
    if not room: db.close(); raise HTTPException(404,"Invalid room code")
    db.close()
    return {"status":"joined","room_id":room["id"],"question":room["question"]}

@app.post("/rooms/{room_id}/speak")
def speak(room_id: str, r: SpeakReq, user: dict=Depends(get_user)):
    text=sanitize(r.text,1000)
    if not text: raise HTTPException(400,"Message required")
    db=get_db(); room=db.execute("SELECT * FROM rooms WHERE id=?",(room_id,)).fetchone()
    if not room: db.close(); raise HTTPException(404,"Room not found")
    mid=str(uuid.uuid4())[:8]
    db.execute("INSERT INTO room_messages VALUES (?,?,?,?,?,?,?,?,?,?)",
               (mid,room_id,user["id"],user["username"],user["avatar"],
                user.get("avatar_color","#8b5cf6"),text,5,1,datetime.datetime.now().isoformat()))
    db.execute("UPDATE scores SET total=total+5 WHERE user_id=?",(user["id"],))
    db.execute("UPDATE users SET echo_coins=echo_coins+2 WHERE id=?",(user["id"],))
    db.commit()
    msgs=db.execute("SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 50",(room_id,)).fetchall()
    db.close()
    return {"ai_response":"What is the strongest counter-argument to your point?",
            "room_messages":[dict(m) for m in msgs]}

@app.get("/rooms/{room_id}/messages")
def get_messages(room_id: str):
    db=get_db(); msgs=db.execute("SELECT * FROM room_messages WHERE room_id=? ORDER BY timestamp ASC LIMIT 100",(room_id,)).fetchall(); db.close()
    return {"messages":[dict(m) for m in msgs]}

# ══════════════════════════════════════════════════════════════════
#  MARKET
# ══════════════════════════════════════════════════════════════════
@app.get("/market")
def get_market(user: dict=Depends(get_user)):
    db=get_db(); items=db.execute("SELECT * FROM market_items ORDER BY price ASC").fetchall()
    owned={row["item_id"] for row in db.execute("SELECT item_id FROM user_inventory WHERE user_id=?",(user["id"],)).fetchall()}
    db.close()
    return {"items":[{**dict(i),"owned":i["id"] in owned} for i in items],"your_coins":user.get("echo_coins",0)}

@app.post("/market/buy")
def buy_item(r: PurchaseReq, user: dict=Depends(get_user)):
    db=get_db(); item=db.execute("SELECT * FROM market_items WHERE id=?",(r.item_id,)).fetchone()
    if not item: db.close(); raise HTTPException(404,"Item not found")
    if db.execute("SELECT item_id FROM user_inventory WHERE user_id=? AND item_id=?",(user["id"],r.item_id)).fetchone():
        db.close(); raise HTTPException(400,"Already owned")
    coins=user.get("echo_coins",0)
    if coins<item["price"]: db.close(); raise HTTPException(400,f"Not enough coins. Need {item['price']}, have {coins}")
    db.execute("UPDATE users SET echo_coins=echo_coins-? WHERE id=?",(item["price"],user["id"]))
    db.execute("INSERT INTO user_inventory VALUES (?,?,?)",(user["id"],r.item_id,datetime.datetime.now().isoformat()))
    db.commit()
    new_coins=db.execute("SELECT echo_coins FROM users WHERE id=?",(user["id"],)).fetchone()[0]
    db.close()
    return {"status":"purchased","item":dict(item),"coins_left":new_coins}

# ══════════════════════════════════════════════════════════════════
#  REELS
# ══════════════════════════════════════════════════════════════════
@app.get("/reels")
def get_reels():
    db=get_db(); reels=db.execute("SELECT * FROM reels ORDER BY timestamp DESC LIMIT 20").fetchall(); db.close()
    return {"reels":[dict(r) for r in reels]}

@app.post("/reels")
def post_reel(r: ReelReq, user: dict=Depends(get_user)):
    if not r.video_url: raise HTTPException(400,"Video URL required")
    db=get_db(); rid=str(uuid.uuid4())[:8]
    db.execute("INSERT INTO reels VALUES (?,?,?,?,?,?,?,?,?,?)",
               (rid,user["id"],user["username"],user["avatar"],r.video_url,
                sanitize(r.caption or "",300),r.topic or "",0,0,datetime.datetime.now().isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins+10 WHERE id=?",(user["id"],))
    db.commit(); db.close()
    return {"status":"ok","reel_id":rid,"coins_earned":10}

@app.post("/reels/{reel_id}/like")
def like_reel(reel_id: str, user: dict=Depends(get_user)):
    db=get_db(); db.execute("UPDATE reels SET likes=likes+1 WHERE id=?",(reel_id,)); db.commit(); db.close()
    return {"status":"ok"}

# ══════════════════════════════════════════════════════════════════
#  BATTLES
# ══════════════════════════════════════════════════════════════════
@app.post("/battles/challenge")
def challenge_battle(r: BattleReq, user: dict=Depends(get_user)):
    if r.challenged_id==user["id"]: raise HTTPException(400,"Cannot challenge yourself")
    db=get_db(); target=db.execute("SELECT * FROM users WHERE id=?",(r.challenged_id,)).fetchone()
    if not target: db.close(); raise HTTPException(404,"User not found")
    bid=str(uuid.uuid4())[:8]
    db.execute("INSERT INTO battles VALUES (?,?,?,?,?,?,?,?,?)",
               (bid,user["id"],r.challenged_id,r.topic,"pending","[]","",
                datetime.datetime.now().isoformat(),
                (datetime.datetime.now()+datetime.timedelta(hours=48)).isoformat()))
    db.commit(); db.close()
    return {"battle_id":bid,"status":"challenge_sent","challenged_username":target["username"]}

@app.post("/battles/{battle_id}/accept")
def accept_battle(battle_id: str, user: dict=Depends(get_user)):
    db=get_db(); battle=db.execute("SELECT * FROM battles WHERE id=?",(battle_id,)).fetchone()
    if not battle or battle["challenged"]!=user["id"]: db.close(); raise HTTPException(404,"Battle not found")
    db.execute("UPDATE battles SET status='active' WHERE id=?",(battle_id,)); db.commit(); db.close()
    return {"status":"battle_started","topic":battle["topic"]}

@app.post("/battles/{battle_id}/argue")
def submit_argument(battle_id: str, r: BattleArgReq, user: dict=Depends(get_user)):
    db=get_db(); battle=db.execute("SELECT * FROM battles WHERE id=?",(battle_id,)).fetchone()
    if not battle: db.close(); raise HTTPException(404,"Battle not found")
    if battle["status"]!="active": db.close(); raise HTTPException(400,"Battle not active")
    if user["id"] not in [battle["challenger"],battle["challenged"]]:
        db.close(); raise HTTPException(403,"Not a participant")
    argument=sanitize(r.argument,1000)
    rounds=json.loads(battle["rounds_json"] or "[]")
    role="challenger" if user["id"]==battle["challenger"] else "challenged"
    score={"score":6,"feedback":"Good point. Keep building your argument."}
    rounds.append({"user_id":user["id"],"username":user["username"],"role":role,
                   "argument":argument,"score":score["score"],"feedback":score["feedback"],"round":len(rounds)+1})
    winner=""; status="active"
    if len(rounds)>=10:
        c_score=sum(r2["score"] for r2 in rounds if r2["role"]=="challenger")
        ch_score=sum(r2["score"] for r2 in rounds if r2["role"]=="challenged")
        winner=battle["challenger"] if c_score>ch_score else battle["challenged"]
        status="complete"
        db.execute("UPDATE users SET echo_coins=echo_coins+100 WHERE id=?",(winner,))
        db.execute("UPDATE scores SET total=total+50 WHERE user_id=?",(winner,))
    db.execute("UPDATE battles SET rounds_json=?,status=?,winner=? WHERE id=?",
               (json.dumps(rounds),status,winner,battle_id))
    db.commit(); db.close()
    return {"status":status,"round":len(rounds),"score":score,"winner":winner,"rounds":rounds}

@app.get("/battles/{battle_id}")
def get_battle(battle_id: str):
    db=get_db(); b=db.execute("SELECT * FROM battles WHERE id=?",(battle_id,)).fetchone(); db.close()
    if not b: raise HTTPException(404,"Battle not found")
    result=dict(b); result["rounds_json"]=json.loads(result["rounds_json"] or "[]")
    return result

@app.get("/battles/my/list")
def my_battles(user: dict=Depends(get_user)):
    db=get_db()
    battles=db.execute("SELECT * FROM battles WHERE challenger=? OR challenged=? ORDER BY created DESC LIMIT 20",(user["id"],user["id"])).fetchall()
    db.close()
    return {"battles":[dict(b) for b in battles]}

# ══════════════════════════════════════════════════════════════════
#  PARLIAMENT
# ══════════════════════════════════════════════════════════════════
@app.get("/parliament")
def get_parliament():
    db=get_db(); vote=db.execute("SELECT * FROM parliament_votes WHERE active=1 ORDER BY created DESC LIMIT 1").fetchone()
    if not vote: db.close(); return {"active":False}
    total=db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=?",(vote["id"],)).fetchone()[0]
    a_count=db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='a'",(vote["id"],)).fetchone()[0]
    b_count=db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='b'",(vote["id"],)).fetchone()[0]
    db.close()
    return {"active":True,"vote":dict(vote),"total":total,
            "a_count":a_count,"b_count":b_count,
            "a_pct":round(a_count/max(total,1)*100),"b_pct":round(b_count/max(total,1)*100)}

@app.post("/parliament/vote")
def parliament_vote(r: ParliamentVoteReq, user: dict=Depends(get_user)):
    if r.choice not in ["a","b"]: raise HTTPException(400,"Choice must be a or b")
    db=get_db()
    if db.execute("SELECT user_id FROM parliament_responses WHERE vote_id=? AND user_id=?",(r.vote_id,user["id"])).fetchone():
        db.close(); raise HTTPException(400,"Already voted")
    db.execute("INSERT INTO parliament_responses VALUES (?,?,?,?,?)",
               (r.vote_id,user["id"],r.choice,r.country or "",datetime.datetime.now().isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins+15 WHERE id=?",(user["id"],))
    db.commit(); db.close()
    return {"status":"voted","coins_earned":15}

# ══════════════════════════════════════════════════════════════════
#  DNA REPORT
# ══════════════════════════════════════════════════════════════════
@app.get("/dna")
def get_dna(user: dict=Depends(get_user)):
    db=get_db()
    sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user["id"],)).fetchone()
    engs=(sc["engagements"] if sc else 0) or 0
    if engs<5:
        db.close(); return {"ready":False,"engagements":engs,"needed":5,
                            "message":f"Complete {5-engs} more engagements to unlock your Mind DNA"}
    cached=db.execute("SELECT * FROM dna_reports WHERE user_id=?",(user["id"],)).fetchone()
    if cached:
        try:
            gen=datetime.datetime.fromisoformat(cached["generated"])
            if (datetime.datetime.now()-gen).days<7:
                db.close(); return {"ready":True,"report":json.loads(cached["report_json"])}
        except: pass
    db.close()
    traits={"quality":5.0,"empathy":6.0,"logic":5.5,"openness":6.5}
    dominant=max(traits,key=traits.get)
    pmap={"quality":("The Architect","🏛️","You build arguments with precision and care."),
          "empathy":("The Empath","💙","You feel the human side of every debate."),
          "logic":("The Philosopher","🔬","You cut through emotion to find truth."),
          "openness":("The Explorer","🌍","You genuinely seek to understand others.")}
    ptype,icon,desc=pmap[dominant]
    report={"personality_type":ptype,"icon":icon,"description":desc,"scores":traits,
            "debate_style":"Balanced — you adapt your style to the topic",
            "strengths":[f"Your {dominant} score is your superpower",
                         "You consistently engage rather than dismiss",
                         "Showing genuine intellectual curiosity"],
            "biases":["Try to acknowledge opposing points more explicitly",
                      "Challenge your own arguments before submitting"],
            "growth_tip":"Find one thing you genuinely agree with in every opposing view",
            "total_debates":engs,"username":user["username"],
            "rank":get_rank((sc["total"] if sc else 0) or 0)}
    db2=get_db()
    db2.execute("INSERT OR REPLACE INTO dna_reports VALUES (?,?,?)",
               (user["id"],json.dumps(report),datetime.datetime.now().isoformat()))
    db2.commit(); db2.close()
    return {"ready":True,"report":report}

# ══════════════════════════════════════════════════════════════════
#  SHARE CARD
# ══════════════════════════════════════════════════════════════════
@app.get("/sharecard")
def get_share_card(user: dict=Depends(get_user)):
    db=get_db()
    sc=db.execute("SELECT * FROM scores WHERE user_id=?",(user["id"],)).fetchone()
    streak=db.execute("SELECT * FROM streaks WHERE user_id=?",(user["id"],)).fetchone()
    best=db.execute("SELECT response,total_score FROM engagements WHERE user_id=? ORDER BY total_score DESC LIMIT 1",(user["id"],)).fetchone()
    total=(sc["total"] if sc else 0) or 0; db.close()
    return {"username":user["username"],"avatar":user["avatar"],"avatar_color":user.get("avatar_color","#8b5cf6"),
            "rank":get_rank(total),"score":total,"engagements":(sc["engagements"] if sc else 0) or 0,
            "mind_changes":(sc["mind_changes"] if sc else 0) or 0,
            "streak":(streak["current"] if streak else 0) or 0,
            "badges":json.loads((sc["badges"] if sc else "[]") or "[]"),
            "best_response":(best["response"][:120] if best else "") or "",
            "best_score":(best["total_score"] if best else 0) or 0,
            "plus":user.get("plus",0),"theme_color":user.get("theme_color","purple")}

# ══════════════════════════════════════════════════════════════════
#  TIMELINE
# ══════════════════════════════════════════════════════════════════
@app.get("/timeline")
def get_timeline(user: dict=Depends(get_user)):
    db=get_db()
    posts=db.execute("SELECT topic,stance,text,timestamp FROM posts WHERE user_id=? ORDER BY timestamp ASC",(user["id"],)).fetchall()
    db.close()
    timeline={}
    for p in posts:
        t=p["topic"]
        if t not in timeline: timeline[t]=[]
        timeline[t].append({"stance":p["stance"],"text":p["text"][:100],"timestamp":p["timestamp"]})
    changes=[]
    for topic,events in timeline.items():
        stances=[e["stance"] for e in events]
        if len(set(stances))>1:
            changes.append({"topic":topic,"from":stances[0],"to":stances[-1]})
    return {"timeline":timeline,"mind_changes":changes,
            "topics_covered":list(timeline.keys()),"total_posts":len(posts)}

# ══════════════════════════════════════════════════════════════════
#  PLUS
# ══════════════════════════════════════════════════════════════════
@app.get("/plus/features")
def plus_features():
    return {"price_monthly":7,"price_yearly":59,
            "features":["Unlimited engagements","Unlimited private rooms",
                        "All market items","Verified Plus badge","Full AI breakdown",
                        "Score analytics","Weekly personality report",
                        "500 Echo Coins bonus","Priority AI","Early access"]}

@app.post("/plus/activate")
def activate_plus(user: dict=Depends(get_user)):
    db=get_db(); db.execute("UPDATE users SET plus=1,echo_coins=echo_coins+500 WHERE id=?",(user["id"],)); db.commit(); db.close()
    return {"status":"plus_activated","bonus_coins":500}

# ══════════════════════════════════════════════════════════════════
#  ROOT
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def root():
    db=get_db()
    s={"app":"ECHO ROOM","version":"4.0","status":"online",
       "users":db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
       "posts":db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
       "engagements":db.execute("SELECT COUNT(*) FROM engagements").fetchone()[0],
       "rooms":db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]}
    db.close(); return s

if __name__ == "__main__":
    print(f"🌐 ECHO ROOM v4 on port {PORT}")
    uvicorn.run(app,host="0.0.0.0",port=PORT,log_level="warning")
