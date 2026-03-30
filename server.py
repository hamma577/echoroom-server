"""
ECHO ROOM — Backend API
========================
AI social platform where you engage with opposite opinions.
The more open-minded you are, the higher your score.

Endpoints:
  POST /auth/register
  POST /auth/login
  GET  /feed              → personalized opposite-opinion feed
  POST /post              → submit your opinion
  POST /engage/{post_id}  → engage with a post (AI scores your response)
  GET  /score             → your open mind score + breakdown
  GET  /leaderboard       → top open minds globally
  GET  /rooms             → active debate rooms
  POST /rooms/{id}/join   → join a debate room
  POST /rooms/{id}/speak  → speak in a room (AI moderates)
  GET  /profile/{user_id}
  GET  /badges            → earned badges
  POST /report            → report harmful content

Run:
  pip install fastapi uvicorn requests python-jose
  python server.py
"""

import os, json, uuid, datetime, random, re
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as req
import uvicorn

# ── AI ────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3"

def ask_ai(prompt: str, system: str = "") -> str:
    try:
        r = req.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt,
            "system": system, "stream": False
        }, timeout=60)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"AI unavailable: {e}"

def ask_ai_json(prompt: str, system: str = "") -> dict:
    raw = ask_ai(prompt, system)
    try:
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except:
        return {}

# ══════════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title="ECHO ROOM API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── In-memory stores (swap with PostgreSQL in production) ─────────
users_db:       dict = {}
sessions_db:    dict = {}
posts_db:       dict = {}
engagements_db: dict = {}
rooms_db:       dict = {}
scores_db:      dict = {}

# ── Seed some starter posts ───────────────────────────────────────
SEED_POSTS = [
    {"topic": "technology", "stance": "against",
     "text": "Social media has made us lonelier, not more connected. Every 'like' is a substitute for a real conversation we're too anxious to have."},
    {"topic": "work", "stance": "for",
     "text": "Remote work is making people less ambitious. The best career opportunities happen in spontaneous hallway conversations, not on Zoom calls."},
    {"topic": "education", "stance": "against",
     "text": "University degrees are an expensive way to signal you can follow instructions. Most of what you learn there is irrelevant within 5 years."},
    {"topic": "health", "stance": "for",
     "text": "Therapy has become a way for people to avoid taking responsibility for their own choices. Not every problem needs a diagnosis."},
    {"topic": "society", "stance": "against",
     "text": "Hustle culture is a trauma response disguised as productivity. We've normalized exhaustion and called it ambition."},
    {"topic": "technology", "stance": "for",
     "text": "AI will not take your job. It will take the job of someone who refuses to learn how to use AI. The threat is self-inflicted."},
    {"topic": "relationships", "stance": "against",
     "text": "Modern dating has turned human connection into a consumer product. We swipe on people the same way we swipe on shoes."},
    {"topic": "society", "stance": "for",
     "text": "Cancel culture is not censorship. It is consequence culture. Powerful people finally facing accountability isn't oppression."},
]


def _seed_posts():
    for i, p in enumerate(SEED_POSTS):
        pid = f"seed_{i}"
        posts_db[pid] = {
            "id": pid, "user_id": "echo_room_official",
            "username": "EchoRoom", "avatar": "ER",
            "text": p["text"], "topic": p["topic"], "stance": p["stance"],
            "timestamp": datetime.datetime.now().isoformat(),
            "engagements": [], "likes": random.randint(40, 400),
            "is_seed": True,
        }

_seed_posts()


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
    text:   str
    topic:  str        # technology | work | society | relationships | health | education
    stance: str        # for | against | neutral

class EngageReq(BaseModel):
    response:  str     # what the user wrote in response
    post_id:   str

class RoomSpeakReq(BaseModel):
    text: str

class CreateRoomReq(BaseModel):
    topic:    str
    question: str


# ══════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════
def get_user(authorization: str = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(401, "Not authenticated")
    token   = authorization.replace("Bearer ", "")
    user_id = sessions_db.get(token)
    if not user_id or user_id not in users_db:
        raise HTTPException(401, "Invalid token")
    return users_db[user_id]


@app.post("/auth/register")
def register(r: AuthReq):
    if any(u["email"] == r.email for u in users_db.values()):
        raise HTTPException(400, "Email already registered")
    uid   = str(uuid.uuid4())
    token = str(uuid.uuid4())
    users_db[uid] = {
        "id": uid, "username": r.username, "email": r.email,
        "password": r.password,
        "avatar": r.username[:2].upper(),
        "joined": datetime.datetime.now().isoformat(),
        "topics_seen": [],
        "stances": {},       # topic → user's known stance
        "premium": False,
    }
    scores_db[uid] = {
        "total": 0, "engagements": 0, "mind_changes": 0,
        "quality_avg": 0, "badge_ids": [],
        "history": [],
    }
    sessions_db[token] = uid
    return {"token": token, "user_id": uid, "username": r.username}


@app.post("/auth/login")
def login(r: LoginReq):
    user = next((u for u in users_db.values()
                 if u["email"] == r.email and u["password"] == r.password), None)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = str(uuid.uuid4())
    sessions_db[token] = user["id"]
    return {"token": token, "user_id": user["id"], "username": user["username"]}


# ══════════════════════════════════════════════════════════════════
#  FEED  — show opposite opinions
# ══════════════════════════════════════════════════════════════════
@app.get("/feed")
def get_feed(topic: str = "", user: dict = Depends(get_user)):
    """Return posts that challenge the user's known stances."""
    user_stances = user.get("stances", {})
    all_posts    = list(posts_db.values())

    # prioritize posts opposite to user's stances
    def score(post):
        t = post.get("topic", "")
        s = post.get("stance", "")
        user_s = user_stances.get(t, "")
        if user_s and user_s != s:
            return 2    # opposite stance — show first
        if t not in user.get("topics_seen", []):
            return 1    # new topic
        return 0

    filtered = [p for p in all_posts if not topic or p.get("topic") == topic]
    sorted_posts = sorted(filtered, key=score, reverse=True)

    return {
        "posts": sorted_posts[:20],
        "your_stances": user_stances,
        "tip": "Posts are selected to challenge your perspective.",
    }


# ══════════════════════════════════════════════════════════════════
#  POSTS
# ══════════════════════════════════════════════════════════════════
@app.post("/post")
def create_post(r: PostReq, user: dict = Depends(get_user)):
    pid  = str(uuid.uuid4())[:8]
    post = {
        "id": pid, "user_id": user["id"],
        "username": user["username"], "avatar": user["avatar"],
        "text": r.text, "topic": r.topic, "stance": r.stance,
        "timestamp": datetime.datetime.now().isoformat(),
        "engagements": [], "likes": 0, "is_seed": False,
    }
    posts_db[pid] = post

    # record user's stance on this topic
    user["stances"][r.topic] = r.stance

    return {"status": "ok", "post": post}


# ══════════════════════════════════════════════════════════════════
#  ENGAGE  — AI scores the quality of engagement
# ══════════════════════════════════════════════════════════════════
@app.post("/engage/{post_id}")
def engage(post_id: str, r: EngageReq, user: dict = Depends(get_user)):
    """User responds to an opposite opinion. AI scores their open-mindedness."""
    post = posts_db.get(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    system = """You are the ECHO ROOM AI judge. Your job is to score how open-mindedly 
someone engaged with an opinion they likely disagree with.

Score these dimensions:
- quality: Did they actually engage with the argument? (0-10)
- empathy: Did they try to understand the other perspective? (0-10)  
- logic: Did they make logical points rather than emotional attacks? (0-10)
- openness: Did they acknowledge any merit in the opposing view? (0-10)
- mind_shift: On a scale 0-10, how much did their response show genuine reconsideration?

Return ONLY JSON. Be strict — most people score 3-6. Only exceptional responses score 8+."""

    prompt = f"""Score this engagement:

Original opinion: "{post['text']}"

User's response: "{r.response}"

Return ONLY:
{{
  "quality": 0-10,
  "empathy": 0-10,
  "logic": 0-10,
  "openness": 0-10,
  "mind_shift": 0-10,
  "total": 0-50,
  "feedback": "One sentence of honest feedback on their engagement quality",
  "highlight": "The best thing they said (quote it)",
  "weakness": "The weakest part of their engagement",
  "badge_earned": null or "open_mind" or "steel_man" or "empathy_award" or "logic_master"
}}"""

    result = ask_ai_json(prompt, system)

    if not result:
        result = {
            "quality": 5, "empathy": 5, "logic": 5, "openness": 5,
            "mind_shift": 3, "total": 23,
            "feedback": "Decent engagement. Try to acknowledge one point from the other side.",
            "highlight": r.response[:50],
            "weakness": "Could be more specific",
            "badge_earned": None,
        }

    # update score
    sc = scores_db.setdefault(user["id"], {
        "total": 0, "engagements": 0, "mind_changes": 0,
        "quality_avg": 0, "badge_ids": [], "history": []
    })
    sc["total"]       += result.get("total", 0)
    sc["engagements"] += 1
    if result.get("mind_shift", 0) >= 7:
        sc["mind_changes"] += 1

    # running average quality
    prev_avg = sc.get("quality_avg", 0)
    n        = sc["engagements"]
    sc["quality_avg"] = (prev_avg * (n-1) + result.get("quality", 5)) / n

    # badge
    if result.get("badge_earned") and result["badge_earned"] not in sc["badge_ids"]:
        sc["badge_ids"].append(result["badge_earned"])

    sc["history"].append({
        "post_id":   post_id,
        "response":  r.response,
        "score":     result,
        "timestamp": datetime.datetime.now().isoformat(),
    })

    # record engagement on post
    post["engagements"].append({
        "user_id":  user["id"],
        "username": user["username"],
        "response": r.response,
        "score":    result.get("total", 0),
    })

    return {
        "status":    "ok",
        "score":     result,
        "your_total": sc["total"],
        "your_rank":  _get_rank(sc["total"]),
    }


# ══════════════════════════════════════════════════════════════════
#  OPEN MIND SCORE
# ══════════════════════════════════════════════════════════════════
def _get_rank(total: int) -> str:
    if total >= 500:   return "Enlightened"
    if total >= 300:   return "Open Mind"
    if total >= 150:   return "Curious"
    if total >= 50:    return "Awakening"
    return "Echo Chamber"


@app.get("/score")
def get_score(user: dict = Depends(get_user)):
    sc = scores_db.get(user["id"], {
        "total": 0, "engagements": 0, "mind_changes": 0,
        "quality_avg": 0, "badge_ids": [], "history": []
    })
    return {
        "score":        sc.get("total", 0),
        "rank":         _get_rank(sc.get("total", 0)),
        "engagements":  sc.get("engagements", 0),
        "mind_changes": sc.get("mind_changes", 0),
        "quality_avg":  round(sc.get("quality_avg", 0), 1),
        "badges":       sc.get("badge_ids", []),
        "next_rank":    _next_rank(sc.get("total", 0)),
        "percentile":   _percentile(sc.get("total", 0)),
    }


def _next_rank(total: int) -> dict:
    thresholds = [(50,"Awakening"),(150,"Curious"),(300,"Open Mind"),(500,"Enlightened")]
    for t, name in thresholds:
        if total < t:
            return {"name": name, "points_needed": t - total}
    return {"name": "Max rank", "points_needed": 0}


def _percentile(score: int) -> int:
    all_scores = [s.get("total", 0) for s in scores_db.values()]
    if not all_scores:
        return 50
    below = sum(1 for s in all_scores if s < score)
    return round(below / len(all_scores) * 100)


# ══════════════════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════════════════
@app.get("/leaderboard")
def leaderboard():
    entries = []
    for uid, sc in scores_db.items():
        user = users_db.get(uid, {})
        entries.append({
            "user_id":    uid,
            "username":   user.get("username", "Unknown"),
            "avatar":     user.get("avatar", "??"),
            "score":      sc.get("total", 0),
            "rank":       _get_rank(sc.get("total", 0)),
            "engagements": sc.get("engagements", 0),
            "mind_changes": sc.get("mind_changes", 0),
            "badges":     sc.get("badge_ids", []),
        })
    entries.sort(key=lambda x: x["score"], reverse=True)
    return {"leaderboard": entries[:50]}


# ══════════════════════════════════════════════════════════════════
#  DEBATE ROOMS
# ══════════════════════════════════════════════════════════════════
SEED_ROOMS = [
    {"topic": "AI will eliminate more jobs than it creates",     "category": "technology"},
    {"topic": "Social media should be banned for under 16s",     "category": "society"},
    {"topic": "Remote work is better for mental health",         "category": "work"},
    {"topic": "University is no longer worth the cost",          "category": "education"},
    {"topic": "Meat eating is ethically indefensible in 2025",   "category": "ethics"},
]

def _seed_rooms():
    for i, r in enumerate(SEED_ROOMS):
        rid = f"room_{i}"
        rooms_db[rid] = {
            "id": rid, "question": r["topic"], "category": r["category"],
            "participants": [], "messages": [],
            "created": datetime.datetime.now().isoformat(),
            "active": True,
        }

_seed_rooms()


@app.get("/rooms")
def list_rooms():
    return {"rooms": list(rooms_db.values())}


@app.post("/rooms")
def create_room(r: CreateRoomReq, user: dict = Depends(get_user)):
    rid = str(uuid.uuid4())[:8]
    rooms_db[rid] = {
        "id": rid, "question": r.question, "category": r.topic,
        "participants": [user["id"]], "messages": [],
        "created": datetime.datetime.now().isoformat(),
        "active": True,
    }
    return {"room": rooms_db[rid]}


@app.post("/rooms/{room_id}/join")
def join_room(room_id: str, user: dict = Depends(get_user)):
    room = rooms_db.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if user["id"] not in room["participants"]:
        room["participants"].append(user["id"])
    return {"status": "joined", "room": room}


@app.post("/rooms/{room_id}/speak")
def speak_in_room(room_id: str, r: RoomSpeakReq, user: dict = Depends(get_user)):
    """User speaks in a debate room — AI moderates and scores."""
    room = rooms_db.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")

    # AI moderation
    mod_result = ask_ai_json(f"""
You are a debate moderator for ECHO ROOM.
Evaluate this debate contribution:

Topic: "{room['question']}"
Message: "{r.text}"

Return ONLY JSON:
{{
  "approved": true/false,
  "quality_score": 0-10,
  "is_constructive": true/false,
  "ai_response": "A one-sentence moderator observation or question to push thinking further",
  "flag": null or "personal_attack" or "off_topic" or "spam"
}}""", "You are a fair, firm debate moderator. Return JSON only.")

    if not mod_result:
        mod_result = {"approved": True, "quality_score": 5,
                      "is_constructive": True,
                      "ai_response": "Interesting point. What evidence supports this?",
                      "flag": None}

    msg = {
        "id":        str(uuid.uuid4())[:8],
        "user_id":   user["id"],
        "username":  user["username"],
        "avatar":    user["avatar"],
        "text":      r.text,
        "timestamp": datetime.datetime.now().isoformat(),
        "score":     mod_result.get("quality_score", 5),
        "approved":  mod_result.get("approved", True),
    }

    if mod_result.get("approved", True):
        room["messages"].append(msg)
        # add score
        sc = scores_db.setdefault(user["id"], {
            "total":0,"engagements":0,"mind_changes":0,
            "quality_avg":0,"badge_ids":[],"history":[]})
        sc["total"] += mod_result.get("quality_score", 5)

    return {
        "message":      msg,
        "moderation":   mod_result,
        "ai_response":  mod_result.get("ai_response", ""),
        "room_messages": room["messages"][-20:],
    }


# ══════════════════════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════════════════════
@app.get("/profile/{user_id}")
def get_profile(user_id: str):
    user = users_db.get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    sc = scores_db.get(user_id, {})
    return {
        "username":    user["username"],
        "avatar":      user["avatar"],
        "joined":      user["joined"],
        "score":       sc.get("total", 0),
        "rank":        _get_rank(sc.get("total", 0)),
        "engagements": sc.get("engagements", 0),
        "mind_changes": sc.get("mind_changes", 0),
        "badges":      sc.get("badge_ids", []),
        "stances":     user.get("stances", {}),
    }


@app.get("/profile/me/full")
def get_my_profile(user: dict = Depends(get_user)):
    sc = scores_db.get(user["id"], {})
    return {
        **get_profile(user["id"]),
        "history": sc.get("history", [])[-10:],
        "percentile": _percentile(sc.get("total", 0)),
        "next_rank":  _next_rank(sc.get("total", 0)),
    }


# ══════════════════════════════════════════════════════════════════
#  BADGES
# ══════════════════════════════════════════════════════════════════
BADGE_INFO = {
    "open_mind":      {"name": "Open Mind",      "desc": "Genuinely engaged with an opposing view",  "icon": "🧠"},
    "steel_man":      {"name": "Steel Man",       "desc": "Made the strongest possible opposing case", "icon": "⚔️"},
    "empathy_award":  {"name": "Empathy Award",   "desc": "Showed exceptional understanding",          "icon": "💙"},
    "logic_master":   {"name": "Logic Master",    "desc": "Made a perfectly reasoned argument",        "icon": "🔬"},
    "mind_changer":   {"name": "Mind Changer",    "desc": "Changed your stance 3+ times",              "icon": "🔄"},
    "debate_champion":{"name": "Debate Champion", "desc": "Top scorer in a room debate",               "icon": "🏆"},
}

@app.get("/badges")
def get_badges(user: dict = Depends(get_user)):
    sc = scores_db.get(user["id"], {})
    earned = sc.get("badge_ids", [])
    return {
        "earned":   [{**BADGE_INFO.get(b, {}), "id": b} for b in earned],
        "available": [{"id": bid, **info, "earned": bid in earned}
                      for bid, info in BADGE_INFO.items()],
    }


@app.get("/")
def root():
    return {"app": "ECHO ROOM", "version": "1.0",
            "tagline": "Expand your mind. Earn your rank.",
            "users": len(users_db), "posts": len(posts_db)}

"""
ECHO ROOM — Server v4 additions
Add these routes to your existing server_clean.py

New features:
  - Daily streak system
  - Share card generator
  - 1v1 debate battles
  - DNA Mind Report
  - Opinion Evolution Timeline
  - Friend challenges
  - Weekly tournament
  - Echo Parliament vote
  - Push notification triggers

Paste this entire file's routes into server_clean.py
before the if __name__ == "__main__": line
"""

# ── ADD THESE TABLES to init_db() executescript ──────────────────
NEW_TABLES = """
CREATE TABLE IF NOT EXISTS streaks (
    user_id      TEXT PRIMARY KEY,
    current      INTEGER DEFAULT 0,
    longest      INTEGER DEFAULT 0,
    last_active  TEXT DEFAULT '',
    shield_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS battles (
    id           TEXT PRIMARY KEY,
    challenger   TEXT NOT NULL,
    challenged   TEXT NOT NULL,
    topic        TEXT,
    status       TEXT DEFAULT 'pending',
    rounds_json  TEXT DEFAULT '[]',
    winner       TEXT DEFAULT '',
    created      TEXT,
    expires      TEXT
);
CREATE TABLE IF NOT EXISTS challenges (
    id           TEXT PRIMARY KEY,
    from_user    TEXT NOT NULL,
    to_user      TEXT NOT NULL,
    post_id      TEXT,
    coins_bet    INTEGER DEFAULT 20,
    status       TEXT DEFAULT 'pending',
    winner       TEXT DEFAULT '',
    created      TEXT,
    expires      TEXT
);
CREATE TABLE IF NOT EXISTS parliament_votes (
    id           TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    option_a     TEXT,
    option_b     TEXT,
    active       INTEGER DEFAULT 1,
    created      TEXT,
    ends         TEXT
);
CREATE TABLE IF NOT EXISTS parliament_responses (
    vote_id  TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    choice   TEXT,
    country  TEXT DEFAULT '',
    created  TEXT,
    PRIMARY KEY (vote_id, user_id)
);
CREATE TABLE IF NOT EXISTS dna_reports (
    user_id      TEXT PRIMARY KEY,
    report_json  TEXT,
    generated    TEXT
);
CREATE TABLE IF NOT EXISTS tournaments (
    id       TEXT PRIMARY KEY,
    week     TEXT,
    status   TEXT DEFAULT 'active',
    winner   TEXT DEFAULT '',
    scores   TEXT DEFAULT '{}'
);
"""

# ══════════════════════════════════════════════════════════════════
#  STREAK SYSTEM
# ══════════════════════════════════════════════════════════════════

@app.get("/streak")
def get_streak(user: dict = Depends(get_user)):
    db  = get_db()
    row = db.execute("SELECT * FROM streaks WHERE user_id=?",
                     (user["id"],)).fetchone()
    db.close()
    if not row:
        return {"current": 0, "longest": 0, "shield_count": 0,
                "status": "no streak yet"}
    today    = datetime.date.today().isoformat()
    last     = (row["last_active"] or "")[:10]
    current  = row["current"] or 0
    # check if streak is broken
    if last and last < str(datetime.date.today() - datetime.timedelta(days=1)):
        if row["shield_count"] and row["shield_count"] > 0:
            status = "shield_saved"
        else:
            status  = "broken"
            current = 0
    elif last == today:
        status = "done_today"
    else:
        status = "active"
    return {
        "current":      current,
        "longest":      row["longest"] or 0,
        "shield_count": row["shield_count"] or 0,
        "last_active":  last,
        "status":       status,
        "next_reward":  _streak_reward(current + 1),
    }


def _streak_reward(day: int) -> dict:
    if day >= 30: return {"coins": 100, "badge": "streak_legend"}
    if day >= 14: return {"coins": 50,  "badge": "streak_master"}
    if day >= 7:  return {"coins": 25,  "badge": "streak_week"}
    if day >= 3:  return {"coins": 10,  "badge": None}
    return {"coins": 5, "badge": None}


def _update_streak(user_id: str, db):
    today = datetime.date.today().isoformat()
    row   = db.execute("SELECT * FROM streaks WHERE user_id=?",
                       (user_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO streaks VALUES (?,?,?,?,?)",
                   (user_id, 1, 1, today, 0))
        return 1

    last    = (row["last_active"] or "")[:10]
    current = row["current"] or 0
    longest = row["longest"] or 0

    if last == today:
        return current      # already counted today

    yesterday = str(datetime.date.today() - datetime.timedelta(days=1))
    if last == yesterday:
        current += 1        # streak continues
    else:
        # broken — check shield
        if (row["shield_count"] or 0) > 0:
            db.execute("UPDATE streaks SET shield_count=shield_count-1 WHERE user_id=?",
                       (user_id,))
            current += 1
        else:
            current = 1     # reset

    longest = max(longest, current)

    # reward coins
    reward = _streak_reward(current)
    db.execute("UPDATE users SET echo_coins=echo_coins+? WHERE id=?",
               (reward["coins"], user_id))
    db.execute("""UPDATE streaks SET current=?,longest=?,last_active=?
                  WHERE user_id=?""", (current, longest, today, user_id))
    return current


# ══════════════════════════════════════════════════════════════════
#  DNA MIND REPORT
# ══════════════════════════════════════════════════════════════════

@app.get("/dna")
def get_dna_report(user: dict = Depends(get_user)):
    db  = get_db()
    row = db.execute("SELECT * FROM dna_reports WHERE user_id=?",
                     (user["id"],)).fetchone()

    # check if user has enough engagements
    sc = db.execute("SELECT * FROM scores WHERE user_id=?",
                    (user["id"],)).fetchone()
    engagements = (sc["engagements"] if sc else 0) or 0

    if engagements < 5:
        db.close()
        return {"ready": False,
                "message": f"Complete {5 - engagements} more engagements to unlock your Mind DNA",
                "engagements": engagements, "needed": 5}

    # return cached report if generated in last 7 days
    if row:
        try:
            gen = datetime.datetime.fromisoformat(row["generated"])
            if (datetime.datetime.now() - gen).days < 7:
                db.close()
                return {"ready": True, "report": json.loads(row["report_json"]),
                        "generated": row["generated"]}
        except:
            pass

    # generate new report
    engs = db.execute("""SELECT e.response, e.score_json FROM engagements e
                         WHERE e.user_id=? ORDER BY e.timestamp DESC LIMIT 20""",
                      (user["id"],)).fetchall()
    db.close()

    responses_text = "\n".join([f"- {e['response'][:200]}" for e in engs[:10]])
    scores_data    = [json.loads(e["score_json"]) for e in engs if e["score_json"]]

    avg_q = round(sum(s.get("quality",5)  for s in scores_data) / max(len(scores_data),1), 1)
    avg_e = round(sum(s.get("empathy",5)  for s in scores_data) / max(len(scores_data),1), 1)
    avg_l = round(sum(s.get("logic",5)    for s in scores_data) / max(len(scores_data),1), 1)
    avg_o = round(sum(s.get("openness",5) for s in scores_data) / max(len(scores_data),1), 1)

    # determine dominant traits
    traits = {"quality": avg_q, "empathy": avg_e,
              "logic": avg_l, "openness": avg_o}
    dominant = max(traits, key=traits.get)

    personality_map = {
        "quality":  ("The Architect",   "🏛️", "You build arguments with precision and care."),
        "empathy":  ("The Empath",       "💙", "You feel the human side of every debate."),
        "logic":    ("The Philosopher",  "🔬", "You cut through emotion to find truth."),
        "openness": ("The Explorer",     "🌍", "You genuinely seek to understand others."),
    }

    ptype, icon, desc = personality_map[dominant]

    # bias detection
    biases = []
    if avg_e < 4: biases.append("Tends to deprioritize emotional context")
    if avg_l < 4: biases.append("Sometimes relies more on feeling than reasoning")
    if avg_o < 4: biases.append("May struggle to fully consider opposing views")
    if avg_q < 4: biases.append("Engagement depth could be stronger")
    if not biases: biases.append("No significant biases detected — keep it up!")

    report = {
        "personality_type": ptype,
        "icon":             icon,
        "description":      desc,
        "scores": {
            "quality":  avg_q,
            "empathy":  avg_e,
            "logic":    avg_l,
            "openness": avg_o,
        },
        "strengths": [
            f"Your {dominant} score of {traits[dominant]}/10 is your superpower",
            "You consistently engage rather than dismiss",
            f"Top {100 - min(int(traits[dominant]*10), 90)}% of ECHO ROOM debaters",
        ],
        "biases":        biases,
        "debate_style":  _debate_style(avg_q, avg_e, avg_l, avg_o),
        "growth_tip":    _growth_tip(dominant, traits),
        "total_debates": engagements,
        "username":      user["username"],
        "rank":          get_rank((sc["total"] if sc else 0) or 0),
    }

    db2 = get_db()
    db2.execute("INSERT OR REPLACE INTO dna_reports VALUES (?,?,?)",
               (user["id"], json.dumps(report),
                datetime.datetime.now().isoformat()))
    db2.commit()
    db2.close()

    return {"ready": True, "report": report,
            "generated": datetime.datetime.now().isoformat()}


def _debate_style(q, e, l, o) -> str:
    if l > e and l > o: return "Socratic — you use questions to expose contradictions"
    if e > l and e > q: return "Humanist — you connect through shared experience"
    if o > 7:           return "Synthesizer — you build bridges between opposing views"
    if q > 7:           return "Architect — you construct airtight logical frameworks"
    return "Balanced — you adapt your style to the topic"


def _growth_tip(dominant: str, traits: dict) -> str:
    weakest = min(traits, key=traits.get)
    tips = {
        "quality":  "Try to be more specific — name exact examples in your responses",
        "empathy":  "Before responding, ask yourself: how does this feel to someone who disagrees?",
        "logic":    "Challenge your own argument first — what's the strongest counter-point?",
        "openness": "Find one thing you genuinely agree with in every opposing view",
    }
    return tips.get(weakest, "Keep engaging — you're growing every day")


# ══════════════════════════════════════════════════════════════════
#  1v1 DEBATE BATTLES
# ══════════════════════════════════════════════════════════════════

class BattleReq(BaseModel):
    challenged_id: str
    topic:         str

class BattleRoundReq(BaseModel):
    argument: str

@app.post("/battles/challenge")
def challenge_battle(r: BattleReq, user: dict = Depends(get_user)):
    if r.challenged_id == user["id"]:
        raise HTTPException(400, "Cannot challenge yourself")
    db  = get_db()
    target = db.execute("SELECT * FROM users WHERE id=?", (r.challenged_id,)).fetchone()
    if not target:
        db.close()
        raise HTTPException(404, "User not found")
    bid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO battles VALUES (?,?,?,?,?,?,?,?,?)",
               (bid, user["id"], r.challenged_id, r.topic,
                "pending", "[]", "",
                datetime.datetime.now().isoformat(),
                (datetime.datetime.now() + datetime.timedelta(hours=24)).isoformat()))
    db.commit()
    db.close()
    return {"battle_id": bid, "status": "challenge_sent",
            "message": f"Challenge sent to {target['username']}"}


@app.post("/battles/{battle_id}/accept")
def accept_battle(battle_id: str, user: dict = Depends(get_user)):
    db     = get_db()
    battle = db.execute("SELECT * FROM battles WHERE id=?", (battle_id,)).fetchone()
    if not battle or battle["challenged"] != user["id"]:
        db.close()
        raise HTTPException(404, "Battle not found")
    db.execute("UPDATE battles SET status='active' WHERE id=?", (battle_id,))
    db.commit()
    db.close()
    return {"status": "battle_started", "topic": battle["topic"]}


@app.post("/battles/{battle_id}/argue")
def submit_argument(battle_id: str, r: BattleRoundReq,
                    user: dict = Depends(get_user)):
    db     = get_db()
    battle = db.execute("SELECT * FROM battles WHERE id=?", (battle_id,)).fetchone()
    if not battle:
        db.close()
        raise HTTPException(404, "Battle not found")
    if battle["status"] != "active":
        db.close()
        raise HTTPException(400, "Battle not active")
    if user["id"] not in [battle["challenger"], battle["challenged"]]:
        db.close()
        raise HTTPException(403, "Not a participant")

    argument   = sanitize(r.argument, 1000)
    rounds     = json.loads(battle["rounds_json"] or "[]")
    role       = "challenger" if user["id"] == battle["challenger"] else "challenged"

    # score the argument
    score_result = ask_ai_json(f"""Score this debate argument on topic: "{battle['topic']}"
Argument: "{argument}"
Return ONLY JSON: {{"score": 0-10, "feedback": "one sentence", "strongest_point": "quote"}}
""", "You are a strict debate judge. Return JSON only.")

    if not score_result:
        score_result = {"score": 5, "feedback": "Decent argument.", "strongest_point": argument[:50]}

    rounds.append({
        "user_id":  user["id"],
        "username": user["username"],
        "role":     role,
        "argument": argument,
        "score":    score_result.get("score", 5),
        "feedback": score_result.get("feedback", ""),
        "round":    len(rounds) + 1,
    })

    # check if battle complete (5 rounds each = 10 total)
    winner = ""
    status = "active"
    if len(rounds) >= 10:
        c_score  = sum(r["score"] for r in rounds if r["role"] == "challenger")
        ch_score = sum(r["score"] for r in rounds if r["role"] == "challenged")
        winner   = battle["challenger"] if c_score > ch_score else battle["challenged"]
        status   = "complete"
        # award winner
        db.execute("UPDATE users SET echo_coins=echo_coins+100 WHERE id=?", (winner,))
        db.execute("UPDATE scores SET total=total+50 WHERE user_id=?", (winner,))

    db.execute("UPDATE battles SET rounds_json=?,status=?,winner=? WHERE id=?",
               (json.dumps(rounds), status, winner, battle_id))
    db.commit()
    db.close()

    return {
        "status":    status,
        "round":     len(rounds),
        "score":     score_result,
        "winner":    winner,
        "rounds":    rounds,
    }


@app.get("/battles/{battle_id}")
def get_battle(battle_id: str):
    db     = get_db()
    battle = db.execute("SELECT * FROM battles WHERE id=?", (battle_id,)).fetchone()
    db.close()
    if not battle: raise HTTPException(404, "Battle not found")
    b = dict(battle)
    b["rounds_json"] = json.loads(b["rounds_json"] or "[]")
    return b


@app.get("/battles/my/list")
def my_battles(user: dict = Depends(get_user)):
    db      = get_db()
    battles = db.execute("""SELECT * FROM battles
                            WHERE challenger=? OR challenged=?
                            ORDER BY created DESC LIMIT 20""",
                         (user["id"], user["id"])).fetchall()
    db.close()
    return {"battles": [dict(b) for b in battles]}


# ══════════════════════════════════════════════════════════════════
#  FRIEND CHALLENGES
# ══════════════════════════════════════════════════════════════════

class ChallengeReq(BaseModel):
    to_user_id: str
    post_id:    str
    coins_bet:  int = 20

@app.post("/challenges/send")
def send_challenge(r: ChallengeReq, user: dict = Depends(get_user)):
    if r.coins_bet > (user.get("echo_coins", 0)):
        raise HTTPException(400, "Not enough coins to bet")
    db  = get_db()
    cid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO challenges VALUES (?,?,?,?,?,?,?,?,?)",
               (cid, user["id"], r.to_user_id, r.post_id, r.coins_bet,
                "pending", "",
                datetime.datetime.now().isoformat(),
                (datetime.datetime.now() + datetime.timedelta(hours=48)).isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins-? WHERE id=?",
               (r.coins_bet, user["id"]))
    db.commit()
    db.close()
    return {"challenge_id": cid, "status": "sent",
            "message": f"Challenge sent! {r.coins_bet} coins at stake."}


@app.get("/challenges/inbox")
def challenges_inbox(user: dict = Depends(get_user)):
    db   = get_db()
    rows = db.execute("""SELECT c.*,u.username as from_username
                         FROM challenges c JOIN users u ON c.from_user=u.id
                         WHERE c.to_user=? AND c.status='pending'""",
                      (user["id"],)).fetchall()
    db.close()
    return {"challenges": [dict(r) for r in rows]}


# ══════════════════════════════════════════════════════════════════
#  ECHO PARLIAMENT
# ══════════════════════════════════════════════════════════════════

class ParliamentVoteReq(BaseModel):
    vote_id: str
    choice:  str   # "a" or "b"
    country: Optional[str] = ""

@app.get("/parliament")
def get_parliament():
    db   = get_db()
    vote = db.execute("SELECT * FROM parliament_votes WHERE active=1 ORDER BY created DESC LIMIT 1").fetchone()
    if not vote:
        db.close()
        return {"active": False, "message": "No active vote right now"}

    total   = db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=?",
                         (vote["id"],)).fetchone()[0]
    a_count = db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='a'",
                         (vote["id"],)).fetchone()[0]
    b_count = db.execute("SELECT COUNT(*) FROM parliament_responses WHERE vote_id=? AND choice='b'",
                         (vote["id"],)).fetchone()[0]

    # country breakdown
    countries = db.execute("""SELECT country, choice, COUNT(*) as cnt
                               FROM parliament_responses WHERE vote_id=? AND country!=''
                               GROUP BY country, choice ORDER BY cnt DESC LIMIT 20""",
                            (vote["id"],)).fetchall()
    db.close()

    return {
        "active":   True,
        "vote":     dict(vote),
        "total":    total,
        "a_count":  a_count,
        "b_count":  b_count,
        "a_pct":    round(a_count/max(total,1)*100),
        "b_pct":    round(b_count/max(total,1)*100),
        "countries": [dict(c) for c in countries],
        "ends":     vote["ends"],
    }


@app.post("/parliament/vote")
def parliament_vote(r: ParliamentVoteReq, user: dict = Depends(get_user)):
    if r.choice not in ["a", "b"]:
        raise HTTPException(400, "Choice must be 'a' or 'b'")
    db   = get_db()
    vote = db.execute("SELECT * FROM parliament_votes WHERE id=? AND active=1",
                      (r.vote_id,)).fetchone()
    if not vote:
        db.close()
        raise HTTPException(404, "Vote not found or ended")
    existing = db.execute("SELECT user_id FROM parliament_responses WHERE vote_id=? AND user_id=?",
                           (r.vote_id, user["id"])).fetchone()
    if existing:
        db.close()
        raise HTTPException(400, "Already voted")
    db.execute("INSERT INTO parliament_responses VALUES (?,?,?,?,?)",
               (r.vote_id, user["id"], r.choice,
                r.country or "", datetime.datetime.now().isoformat()))
    db.execute("UPDATE users SET echo_coins=echo_coins+15 WHERE id=?", (user["id"],))
    db.commit()
    db.close()
    return {"status": "voted", "coins_earned": 15}


@app.post("/parliament/create")
def create_parliament_vote(question: str, option_a: str, option_b: str,
                            user: dict = Depends(get_user)):
    db  = get_db()
    vid = str(uuid.uuid4())[:8]
    db.execute("INSERT INTO parliament_votes VALUES (?,?,?,?,?,?,?)",
               (vid, question, option_a, option_b, 1,
                datetime.datetime.now().isoformat(),
                (datetime.datetime.now() + datetime.timedelta(days=7)).isoformat()))
    db.commit()
    db.close()
    return {"vote_id": vid, "status": "created"}


# ══════════════════════════════════════════════════════════════════
#  SHARE CARD DATA
# ══════════════════════════════════════════════════════════════════

@app.get("/sharecard")
def get_share_card(user: dict = Depends(get_user)):
    """Returns all data needed to render a beautiful share card."""
    db = get_db()
    sc = db.execute("SELECT * FROM scores WHERE user_id=?",
                    (user["id"],)).fetchone()
    streak = db.execute("SELECT * FROM streaks WHERE user_id=?",
                        (user["id"],)).fetchone()

    # best engagement
    best = db.execute("""SELECT e.response, e.total_score FROM engagements e
                         WHERE e.user_id=? ORDER BY e.total_score DESC LIMIT 1""",
                      (user["id"],)).fetchone()

    total = (sc["total"] if sc else 0) or 0
    db.close()

    return {
        "username":       user["username"],
        "avatar":         user["avatar"],
        "avatar_color":   user.get("avatar_color", "#8b5cf6"),
        "rank":           get_rank(total),
        "score":          total,
        "engagements":    (sc["engagements"] if sc else 0) or 0,
        "mind_changes":   (sc["mind_changes"] if sc else 0) or 0,
        "streak":         (streak["current"] if streak else 0) or 0,
        "badges":         json.loads((sc["badges"] if sc else "[]") or "[]"),
        "best_response":  (best["response"][:120] if best else "") or "",
        "best_score":     (best["total_score"] if best else 0) or 0,
        "plus":           user.get("plus", 0),
        "theme_color":    user.get("theme_color", "purple"),
        "tagline":        "Expand your mind. Earn your rank.",
    }


# ══════════════════════════════════════════════════════════════════
#  OPINION EVOLUTION TIMELINE
# ══════════════════════════════════════════════════════════════════

@app.get("/timeline")
def get_timeline(user: dict = Depends(get_user)):
    """Returns user's opinion history organized by topic over time."""
    db    = get_db()
    posts = db.execute("""SELECT topic, stance, text, timestamp
                          FROM posts WHERE user_id=? ORDER BY timestamp ASC""",
                       (user["id"],)).fetchall()
    engs  = db.execute("""SELECT e.total_score, e.timestamp, p.topic, p.text
                          FROM engagements e JOIN posts p ON e.post_id=p.id
                          WHERE e.user_id=? ORDER BY e.timestamp ASC""",
                       (user["id"],)).fetchall()
    db.close()

    # group by topic
    timeline = {}
    for p in posts:
        t = p["topic"]
        if t not in timeline:
            timeline[t] = []
        timeline[t].append({
            "type":      "post",
            "stance":    p["stance"],
            "text":      p["text"][:100],
            "timestamp": p["timestamp"],
        })

    # detect mind changes (same topic, different stance over time)
    changes = []
    for topic, events in timeline.items():
        stances = [e["stance"] for e in events if "stance" in e]
        if len(set(stances)) > 1:
            changes.append({
                "topic":   topic,
                "changed": f"You changed your stance on {topic}",
                "from":    stances[0],
                "to":      stances[-1],
            })

    return {
        "timeline":    timeline,
        "mind_changes": changes,
        "total_posts":  len(posts),
        "topics_covered": list(timeline.keys()),
        "most_active_topic": max(timeline, key=lambda k: len(timeline[k])) if timeline else "",
    }


# ══════════════════════════════════════════════════════════════════
#  TOURNAMENT
# ══════════════════════════════════════════════════════════════════

@app.get("/tournament")
def get_tournament():
    week = datetime.date.today().strftime("%Y-W%V")
    db   = get_db()
    t    = db.execute("SELECT * FROM tournaments WHERE week=?", (week,)).fetchone()
    if not t:
        tid = str(uuid.uuid4())[:8]
        db.execute("INSERT INTO tournaments VALUES (?,?,?,?,?)",
                   (tid, week, "active", "", "{}"))
        db.commit()
        t = db.execute("SELECT * FROM tournaments WHERE week=?", (week,)).fetchone()

    # get top 10 scores this week
    top = db.execute("""SELECT u.username, u.avatar, u.avatar_color, s.total
                        FROM scores s JOIN users u ON s.user_id=u.id
                        ORDER BY s.total DESC LIMIT 10""").fetchall()
    db.close()

    days_left = 7 - datetime.date.today().weekday()
    return {
        "week":      week,
        "status":    t["status"],
        "days_left": days_left,
        "top_10":    [dict(r) for r in top],
        "prize":     {"coins": 1000, "badge": "tournament_champion"},
        "your_rank": None,
    }


# ══════════════════════════════════════════════════════════════════
#  ENHANCED ENGAGE (with streak update)
# ══════════════════════════════════════════════════════════════════
# NOTE: In your server_clean.py, add this line inside the engage()
# function, right before db.commit():
#
#   _update_streak(user["id"], db)
#
# This tracks the daily streak every time someone engages.
if __name__ == "__main__":
    print("🌐 ECHO ROOM API starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

