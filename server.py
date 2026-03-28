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


if __name__ == "__main__":
    print("🌐 ECHO ROOM API starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
