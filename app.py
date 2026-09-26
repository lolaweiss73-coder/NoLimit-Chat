from __future__ import annotations

import asyncio
import json
import hashlib
import secrets
import io
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "chat.db"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="No Limit Chat", version="0.1.1")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = db()
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            is_private INTEGER NOT NULL DEFAULT 0,
            password TEXT NOT NULL DEFAULT '',
            allowed_gender TEXT NOT NULL DEFAULT 'all',
            min_age INTEGER NOT NULL DEFAULT 18,
            max_age INTEGER NOT NULL DEFAULT 99,
            region TEXT NOT NULL DEFAULT 'all',
            relationship_status TEXT NOT NULL DEFAULT 'all',
            created_at REAL NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            reporter_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE TABLE IF NOT EXISTS identities (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, nickname TEXT NOT NULL, age INTEGER NOT NULL, gender TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', dm_policy TEXT NOT NULL DEFAULT 'any', created_at REAL NOT NULL)")
    if "dm_policy" not in {column[1] for column in con.execute("PRAGMA table_info(identities)")}:
        con.execute("ALTER TABLE identities ADD COLUMN dm_policy TEXT NOT NULL DEFAULT 'any'")
    con.execute("CREATE TABLE IF NOT EXISTS identity_blocks (blocker_id TEXT NOT NULL, blocked_id TEXT NOT NULL, PRIMARY KEY(blocker_id,blocked_id))")
    con.execute("CREATE TABLE IF NOT EXISTS friendships (requester_id TEXT NOT NULL, recipient_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, PRIMARY KEY(requester_id,recipient_id))")
    con.execute("CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, visibility TEXT NOT NULL, created_at REAL NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS photo_grants (photo_id TEXT NOT NULL, recipient_id TEXT NOT NULL, PRIMARY KEY(photo_id,recipient_id))")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL, read_at REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS messages_recipient ON messages(recipient_id,created_at)")
    existing = con.execute("SELECT COUNT(*) AS c FROM rooms").fetchone()["c"]
    if not existing:
        now = time.time()
        rooms = [
            ("general", "כללי", "החדר הראשי", 0, "", "all", 18, 99, "all", "all", now),
            ("dating", "היכרויות", "היכרות ושיחות", 0, "", "all", 18, 99, "all", "all", now),
            ("kink", "קינק", "שיחות קינקיות למבוגרים", 0, "", "all", 18, 99, "all", "all", now),
        ]
        con.executemany(
            "INSERT INTO rooms (id,name,description,is_private,password,allowed_gender,min_age,max_age,region,relationship_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rooms,
        )
    con.commit()
    con.close()


init_db()


class Hub:
    def __init__(self) -> None:
        self.clients: dict[str, WebSocket] = {}
        self.profiles: dict[str, dict[str, Any]] = {}
        self.identity_clients: dict[str, str] = {}
        self.room_members: dict[str, set[str]] = defaultdict(set)
        self.blocked: dict[str, set[str]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def send(self, client_id: str, payload: dict[str, Any]) -> None:
        ws = self.clients.get(client_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def broadcast(self, payload: dict[str, Any], recipients: list[str] | None = None) -> None:
        ids = recipients if recipients is not None else list(self.clients)
        await asyncio.gather(*(self.send(cid, payload) for cid in ids), return_exceptions=True)

    def online_snapshot(self) -> list[dict[str, Any]]:
        result = []
        for cid, p in self.profiles.items():
            item = dict(p)
            item["client_id"] = cid
            result.append(item)
        return result

    def room_snapshot(self) -> list[dict[str, Any]]:
        con = db()
        rows = con.execute(
            "SELECT id,name,description,is_private,allowed_gender,min_age,max_age,region,relationship_status FROM rooms ORDER BY created_at ASC"
        ).fetchall()
        con.close()
        rooms = []
        for r in rows:
            d = dict(r)
            d["is_private"] = bool(d["is_private"])
            d["online"] = len(self.room_members.get(d["id"], set()))
            rooms.append(d)
        return rooms

    def can_contact(self, sender_id: str, target_id: str) -> tuple[bool, str]:
        if target_id not in self.profiles:
            return False, "המשתמש כבר לא מחובר"
        if sender_id in self.blocked.get(target_id, set()) or target_id in self.blocked.get(sender_id, set()):
            return False, "התקשורת בין המשתמשים חסומה"
        target = self.profiles[target_id]
        sender = self.profiles.get(sender_id, {})
        con = db()
        blocked = con.execute("SELECT 1 FROM identity_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",(sender.get("identity_id"),target.get("identity_id"),target.get("identity_id"),sender.get("identity_id"))).fetchone()
        con.close()
        if blocked:
            return False, "התקשורת בין המשתמשים חסומה"
        if target.get("dnd"):
            return False, "המשתמש במצב נא לא להפריע"
        policy = target.get("dm_policy", "any")
        if policy == "none":
            return False, "המשתמש אינו מקבל הודעות פרטיות"
        if policy == "female" and sender.get("gender") != "female":
            return False, "המשתמש מקבל כרגע פניות מנשים בלבד"
        if policy == "male" and sender.get("gender") != "male":
            return False, "המשתמש מקבל כרגע פניות מגברים בלבד"
        return True, ""

    def identity_for(self, client_id: str) -> str:
        return self.profiles[client_id]["identity_id"]

    def conversation(self, identity_id: str, other_id: str) -> list[dict[str, Any]]:
        con = db()
        rows = con.execute("SELECT m.id,m.sender_id,m.recipient_id,m.text,m.created_at,m.read_at,i.nickname FROM messages m JOIN identities i ON i.id=m.sender_id WHERE (m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?) ORDER BY m.created_at DESC LIMIT 100", (identity_id,other_id,other_id,identity_id)).fetchall()
        con.close()
        return [{"id":r["id"],"from_identity":r["sender_id"],"to_identity":r["recipient_id"],"text":r["text"],"ts":r["created_at"],"read_at":r["read_at"],"profile":{"nickname":r["nickname"]}} for r in reversed(rows)]

    def inbox(self, identity_id: str) -> list[dict[str, Any]]:
        con = db()
        rows = con.execute("SELECT CASE WHEN sender_id=? THEN recipient_id ELSE sender_id END AS other_id,MAX(created_at) AS last_ts,SUM(CASE WHEN recipient_id=? AND read_at IS NULL THEN 1 ELSE 0 END) AS unread FROM messages WHERE sender_id=? OR recipient_id=? GROUP BY other_id ORDER BY last_ts DESC", (identity_id,identity_id,identity_id,identity_id)).fetchall()
        result = []
        for r in rows:
            person = con.execute("SELECT nickname FROM identities WHERE id=?",(r["other_id"],)).fetchone()
            result.append({"identity_id":r["other_id"],"nickname":person["nickname"] if person else "משתמש", "unread":r["unread"],"last_ts":r["last_ts"]})
        con.close()
        return result

    def friendships(self, identity_id: str) -> list[dict[str, Any]]:
        con = db()
        rows = con.execute("SELECT f.requester_id,f.recipient_id,f.status,i.nickname FROM friendships f JOIN identities i ON i.id=CASE WHEN f.requester_id=? THEN f.recipient_id ELSE f.requester_id END WHERE f.requester_id=? OR f.recipient_id=? ORDER BY f.created_at DESC",(identity_id,identity_id,identity_id)).fetchall()
        con.close()
        return [{"identity_id":r["recipient_id"] if r["requester_id"]==identity_id else r["requester_id"],"nickname":r["nickname"],"status":r["status"],"incoming":r["recipient_id"]==identity_id} for r in rows]

    def room_allowed(self, room_id: str, profile: dict[str, Any], password: str = "") -> tuple[bool, str]:
        con = db()
        row = con.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        con.close()
        if not row:
            return False, "החדר לא קיים"
        age = int(profile.get("age", 0) or 0)
        gender = profile.get("gender", "")
        region = profile.get("region", "")
        status = profile.get("relationship_status", "")
        if row["is_private"] and row["password"] and password != row["password"]:
            return False, "סיסמה שגויה"
        if row["allowed_gender"] != "all" and gender != row["allowed_gender"]:
            return False, "החדר מוגבל למגדר אחר"
        if age < row["min_age"] or age > row["max_age"]:
            return False, "הגיל שלך אינו בטווח החדר"
        if row["region"] != "all" and region != row["region"]:
            return False, "החדר מוגבל לאזור אחר"
        if row["relationship_status"] != "all" and status != row["relationship_status"]:
            return False, "החדר מוגבל לסטטוס זוגי אחר"
        return True, ""


hub = Hub()


def identity_from_header(authorization: str | None) -> str:
    token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(401, "Identity token required")
    con = db()
    row = con.execute("SELECT id FROM identities WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    con.close()
    if not row: raise HTTPException(401, "Invalid identity token")
    return row["id"]


def visible_photos(viewer_id: str, owner_id: str) -> list[dict[str, Any]]:
    con = db()
    rows = con.execute("SELECT p.id,p.visibility FROM photos p WHERE p.owner_id=? AND (p.visibility='public' OR p.owner_id=? OR EXISTS (SELECT 1 FROM photo_grants g WHERE g.photo_id=p.id AND g.recipient_id=?)) ORDER BY p.created_at",(owner_id,viewer_id,viewer_id)).fetchall()
    con.close()
    return [dict(row) for row in rows]


@app.post("/api/photos")
async def upload_photo(file: UploadFile = File(...), visibility: str = "private", authorization: str | None = Header(default=None)):
    owner_id = identity_from_header(authorization)
    if visibility not in {"public","private"}: raise HTTPException(400, "Invalid visibility")
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024: raise HTTPException(413, "Image too large")
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
        if img.width * img.height > 20_000_000: raise HTTPException(413, "Image dimensions too large")
        img.thumbnail((1600,1600))
        if img.mode not in {"RGB","L"}: img = img.convert("RGB")
        output = io.BytesIO()
        img.save(output,format="JPEG",quality=85)
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "Invalid image")
    photo_id = uuid.uuid4().hex
    (UPLOAD_DIR / photo_id).write_bytes(output.getvalue())
    con = db()
    con.execute("INSERT INTO photos (id,owner_id,visibility,created_at) VALUES (?,?,?,?)",(photo_id,owner_id,visibility,time.time()))
    con.commit(); con.close()
    return {"id":photo_id,"visibility":visibility}


@app.get("/api/photos/{photo_id}")
async def read_photo(photo_id: str, authorization: str | None = Header(default=None)):
    viewer_id = identity_from_header(authorization)
    con = db()
    row = con.execute("SELECT owner_id FROM photos WHERE id=?",(photo_id,)).fetchone()
    con.close()
    if not row or not any(p["id"] == photo_id for p in visible_photos(viewer_id,row["owner_id"])):
        raise HTTPException(404, "Photo unavailable")
    path = UPLOAD_DIR / photo_id
    if not path.exists(): raise HTTPException(404, "Photo unavailable")
    return Response(path.read_bytes(),media_type="image/jpeg",headers={"Cache-Control":"private, no-store"})


@app.get("/api/profiles/{owner_id}/photos")
async def list_photos(owner_id: str, authorization: str | None = Header(default=None)):
    return visible_photos(identity_from_header(authorization),owner_id)


@app.post("/api/photos/{photo_id}/share/{recipient_id}")
async def share_photo(photo_id: str, recipient_id: str, authorization: str | None = Header(default=None)):
    owner_id = identity_from_header(authorization)
    con = db()
    photo = con.execute("SELECT visibility FROM photos WHERE id=? AND owner_id=?",(photo_id,owner_id)).fetchone()
    recipient = con.execute("SELECT id FROM identities WHERE id=?",(recipient_id,)).fetchone()
    if not photo or not recipient or photo["visibility"] != "private":
        con.close(); raise HTTPException(404, "Photo or recipient unavailable")
    con.execute("INSERT OR IGNORE INTO photo_grants (photo_id,recipient_id) VALUES (?,?)",(photo_id,recipient_id))
    con.commit(); con.close()
    return {"ok":True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
async def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
async def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "online": len(hub.clients), "rooms": len(hub.room_snapshot())})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id = str(uuid.uuid4())
    async with hub.lock:
        hub.clients[client_id] = ws
    await ws.send_json({"type": "connected", "client_id": client_id})

    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")

            if mtype == "hello":
                if client_id in hub.profiles:
                    await hub.send(client_id, {"type": "error", "message": "כבר התחברת"})
                    continue
                profile = data.get("profile") or {}
                nickname = str(profile.get("nickname", "")).strip()[:40]
                try:
                    age = int(profile.get("age", 0) or 0)
                except (ValueError, TypeError):
                    age = 0
                gender = profile.get("gender")
                if not nickname or age < 18 or gender not in {"female", "male", "other"}:
                    await hub.send(client_id, {"type": "error", "message": "יש למלא כינוי, גיל 18+ ומגדר"})
                    continue
                token = str(data.get("identity_token") or "")
                token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
                con = db()
                identity = con.execute("SELECT id,nickname,age,gender,description,dm_policy FROM identities WHERE token_hash=?", (token_hash,)).fetchone() if token else None
                if identity:
                    identity_id = identity["id"]
                    nickname, age, gender = identity["nickname"], identity["age"], identity["gender"]
                else:
                    token = secrets.token_urlsafe(32)
                    identity_id = str(uuid.uuid4())
                    policy = profile.get("dm_policy", "any")
                    if policy not in {"any","female","male","none"}: policy = "any"
                    con.execute("INSERT INTO identities (id,token_hash,nickname,age,gender,dm_policy,created_at) VALUES (?,?,?,?,?,?,?)",(identity_id,hashlib.sha256(token.encode()).hexdigest(),nickname,age,gender,policy,time.time()))
                    con.commit()
                con.close()
                clean = {
                    "identity_id": identity_id,
                    "description": identity["description"] if identity else "",
                    "nickname": nickname,
                    "age": min(age, 99),
                    "gender": gender,
                    "interested_in": profile.get("interested_in", "all"),
                    "relationship_status": profile.get("relationship_status", "unspecified"),
                    "region": profile.get("region", "unspecified"),
                    "dnd": bool(profile.get("dnd", False)),
                    "dm_policy": identity["dm_policy"] if identity else policy,
                    "joined_at": time.time(),
                }
                async with hub.lock:
                    hub.profiles[client_id] = clean
                    hub.identity_clients[identity_id] = client_id
                    hub.room_members["general"].add(client_id)
                await hub.send(client_id, {
                    "type": "bootstrap",
                    "self": {**clean, "client_id": client_id},
                    "users": hub.online_snapshot(),
                    "rooms": hub.room_snapshot(),
                    "active_room": "general",
                    "identity_token": token,
                    "inbox": hub.inbox(identity_id),
                    "friends": hub.friendships(identity_id),
                })
                await hub.broadcast({"type": "presence", "users": hub.online_snapshot()})
                await hub.broadcast({"type": "rooms", "rooms": hub.room_snapshot()})
                continue

            if client_id not in hub.profiles:
                await hub.send(client_id, {"type": "error", "message": "יש להתחבר קודם"})
                continue

            if mtype == "profile_update":
                p = hub.profiles[client_id]
                for key in ("interested_in", "relationship_status", "region", "dm_policy"):
                    if key in data:
                        p[key] = data[key]
                if "dnd" in data:
                    p["dnd"] = bool(data["dnd"])
                if "description" in data:
                    p["description"] = str(data["description"]).strip()[:500]
                if p["dm_policy"] not in {"any","female","male","none"}: p["dm_policy"] = "any"
                con = db()
                con.execute("UPDATE identities SET dm_policy=?,description=? WHERE id=?",(p["dm_policy"],p["description"],p["identity_id"]))
                con.commit()
                con.close()
                await hub.broadcast({"type": "presence", "users": hub.online_snapshot()})

            elif mtype == "friend_request":
                target_identity = str(data.get("identity_id", ""))
                sender_identity = hub.identity_for(client_id)
                if target_identity == sender_identity: continue
                con = db()
                target = con.execute("SELECT id FROM identities WHERE id=?",(target_identity,)).fetchone()
                blocked = con.execute("SELECT 1 FROM identity_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",(sender_identity,target_identity,target_identity,sender_identity)).fetchone()
                if target and not blocked:
                    con.execute("INSERT OR IGNORE INTO friendships (requester_id,recipient_id,status,created_at) VALUES (?,?,?,?)",(sender_identity,target_identity,"pending",time.time()))
                    con.commit()
                con.close()
                for identity in (target_identity,sender_identity):
                    online = hub.identity_clients.get(identity)
                    if online: await hub.send(online,{"type":"friends","friends":hub.friendships(identity)})

            elif mtype == "friend_reply":
                requester = str(data.get("identity_id", ""))
                recipient = hub.identity_for(client_id)
                status = "accepted" if data.get("accept") is True else "rejected"
                con = db()
                con.execute("UPDATE friendships SET status=? WHERE requester_id=? AND recipient_id=? AND status='pending'",(status,requester,recipient))
                changed = con.total_changes
                con.commit()
                con.close()
                if changed:
                    for identity in (recipient,requester):
                        online = hub.identity_clients.get(identity)
                        if online: await hub.send(online,{"type":"friends","friends":hub.friendships(identity)})

            elif mtype == "conversation":
                other_id = str(data.get("identity_id", ""))
                identity_id = hub.identity_for(client_id)
                con = db()
                exists = con.execute("SELECT 1 FROM identities WHERE id=?",(other_id,)).fetchone()
                if exists:
                    con.execute("UPDATE messages SET read_at=? WHERE sender_id=? AND recipient_id=? AND read_at IS NULL",(time.time(),other_id,identity_id))
                    con.commit()
                con.close()
                if exists:
                    await hub.send(client_id,{"type":"conversation","identity_id":other_id,"messages":hub.conversation(identity_id,other_id),"inbox":hub.inbox(identity_id)})

            elif mtype == "join_room":
                room_id = str(data.get("room_id", ""))
                ok, reason = hub.room_allowed(room_id, hub.profiles[client_id], str(data.get("password", "")))
                if not ok:
                    await hub.send(client_id, {"type": "error", "message": reason})
                    continue
                async with hub.lock:
                    for members in hub.room_members.values():
                        members.discard(client_id)
                    hub.room_members[room_id].add(client_id)
                await hub.send(client_id, {"type": "joined_room", "room_id": room_id})
                await hub.broadcast({"type": "rooms", "rooms": hub.room_snapshot()})

            elif mtype == "room_message":
                room_id = str(data.get("room_id", ""))
                text = str(data.get("text", "")).strip()[:4000]
                if not text or client_id not in hub.room_members.get(room_id, set()):
                    continue
                payload = {
                    "type": "room_message",
                    "room_id": room_id,
                    "message": {
                        "id": str(uuid.uuid4()),
                        "from": client_id,
                        "profile": hub.profiles[client_id],
                        "text": text,
                        "ts": time.time(),
                    },
                }
                await hub.broadcast(payload, list(hub.room_members[room_id]))

            elif mtype == "dm":
                target_identity = str(data.get("identity_id", ""))
                target_id = hub.identity_clients.get(target_identity) if target_identity else str(data.get("target_id", ""))
                text = str(data.get("text", "")).strip()[:4000]
                if not text:
                    continue
                if target_id:
                    ok, reason = hub.can_contact(client_id, target_id)
                elif target_identity:
                    con = db()
                    exists = con.execute("SELECT gender,dm_policy FROM identities WHERE id=?",(target_identity,)).fetchone()
                    blocked = con.execute("SELECT 1 FROM identity_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",(sender_identity := hub.identity_for(client_id),target_identity,target_identity,sender_identity)).fetchone()
                    con.close()
                    sender_gender = hub.profiles[client_id]["gender"]
                    ok = bool(exists) and not blocked and exists["dm_policy"] != "none" and (exists["dm_policy"] == "any" or exists["dm_policy"] == sender_gender)
                    reason = "לא ניתן לשלוח הודעה למשתמש הזה"
                else:
                    ok, reason = False, "המשתמש לא קיים"
                if not ok:
                    await hub.send(client_id, {"type": "error", "message": reason})
                    continue
                sender_identity = hub.identity_for(client_id)
                target_identity = target_identity or hub.identity_for(target_id)
                msg = {
                    "id": str(uuid.uuid4()),
                    "from": client_id,
                    "to": target_id,
                    "from_identity": sender_identity,
                    "to_identity": target_identity,
                    "profile": hub.profiles[client_id],
                    "text": text,
                    "ts": time.time(),
                }
                con = db()
                con.execute("INSERT INTO messages (id,sender_id,recipient_id,text,created_at) VALUES (?,?,?,?,?)",(msg["id"],sender_identity,target_identity,text,msg["ts"]))
                con.commit()
                con.close()
                if target_id:
                    await hub.send(target_id, {"type": "dm", "message": msg, "inbox": hub.inbox(target_identity)})
                await hub.send(client_id, {"type": "dm", "message": msg, "inbox": hub.inbox(sender_identity)})

            elif mtype == "create_room":
                raw = data.get("room") or {}
                name = str(raw.get("name", "")).strip()[:60]
                if not name:
                    await hub.send(client_id, {"type": "error", "message": "צריך שם לחדר"})
                    continue
                room_id = "r_" + uuid.uuid4().hex[:10]
                allowed_gender = raw.get("allowed_gender", "all")
                if allowed_gender not in {"all", "female", "male", "other"}:
                    allowed_gender = "all"
                try:
                    min_age = max(18, min(99, int(raw.get("min_age", 18) or 18)))
                    max_age = max(min_age, min(99, int(raw.get("max_age", 99) or 99)))
                except (ValueError, TypeError):
                    await hub.send(client_id, {"type": "error", "message": "טווח הגילים אינו תקין"})
                    continue
                is_private = bool(raw.get("is_private", False))
                password = str(raw.get("password", ""))[:100] if is_private else ""
                con = db()
                con.execute(
                    "INSERT INTO rooms (id,name,description,is_private,password,allowed_gender,min_age,max_age,region,relationship_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        room_id,
                        name,
                        str(raw.get("description", ""))[:200],
                        1 if is_private else 0,
                        password,
                        allowed_gender,
                        min_age,
                        max_age,
                        raw.get("region", "all"),
                        raw.get("relationship_status", "all"),
                        time.time(),
                    ),
                )
                con.commit()
                con.close()
                await hub.broadcast({"type": "rooms", "rooms": hub.room_snapshot()})
                await hub.send(client_id, {"type": "room_created", "room_id": room_id})

            elif mtype == "block":
                target_id = str(data.get("target_id", ""))
                if target_id and target_id != client_id:
                    if target_id not in hub.profiles:
                        continue
                    hub.blocked[client_id].add(target_id)
                    hub.blocked[target_id].add(client_id)
                    con = db()
                    con.execute("INSERT OR IGNORE INTO identity_blocks (blocker_id,blocked_id) VALUES (?,?)",(hub.identity_for(client_id),hub.identity_for(target_id)))
                    con.commit()
                    con.close()
                    await hub.send(client_id, {"type": "blocked", "target_id": target_id})
                    await hub.send(target_id, {"type": "blocked_by", "target_id": client_id})

            elif mtype == "report":
                target_id = str(data.get("target_id", ""))
                reason = str(data.get("reason", "other"))[:80]
                details = str(data.get("details", ""))[:1000]
                con = db()
                con.execute(
                    "INSERT INTO reports (id,reporter_id,target_id,reason,details,created_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), client_id, target_id, reason, details, time.time()),
                )
                con.commit()
                con.close()
                await hub.send(client_id, {"type": "report_received", "target_id": target_id})

    except WebSocketDisconnect:
        pass
    finally:
        async with hub.lock:
            identity_id = hub.profiles.get(client_id, {}).get("identity_id")
            if identity_id and hub.identity_clients.get(identity_id) == client_id:
                hub.identity_clients.pop(identity_id, None)
            hub.clients.pop(client_id, None)
            hub.profiles.pop(client_id, None)
            for members in hub.room_members.values():
                members.discard(client_id)
            hub.blocked.pop(client_id, None)
            for s in hub.blocked.values():
                s.discard(client_id)
        await hub.broadcast({"type": "presence", "users": hub.online_snapshot()})
        await hub.broadcast({"type": "rooms", "rooms": hub.room_snapshot()})
