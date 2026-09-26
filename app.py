from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "chat.db"
STATIC_DIR = BASE_DIR / "static"

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
                clean = {
                    "nickname": nickname,
                    "age": min(age, 99),
                    "gender": gender,
                    "interested_in": profile.get("interested_in", "all"),
                    "relationship_status": profile.get("relationship_status", "unspecified"),
                    "region": profile.get("region", "unspecified"),
                    "dnd": bool(profile.get("dnd", False)),
                    "dm_policy": profile.get("dm_policy", "any"),
                    "joined_at": time.time(),
                }
                async with hub.lock:
                    hub.profiles[client_id] = clean
                    hub.room_members["general"].add(client_id)
                await hub.send(client_id, {
                    "type": "bootstrap",
                    "self": {**clean, "client_id": client_id},
                    "users": hub.online_snapshot(),
                    "rooms": hub.room_snapshot(),
                    "active_room": "general",
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
                await hub.broadcast({"type": "presence", "users": hub.online_snapshot()})

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
                target_id = str(data.get("target_id", ""))
                text = str(data.get("text", "")).strip()[:4000]
                if not text:
                    continue
                ok, reason = hub.can_contact(client_id, target_id)
                if not ok:
                    await hub.send(client_id, {"type": "error", "message": reason})
                    continue
                msg = {
                    "id": str(uuid.uuid4()),
                    "from": client_id,
                    "to": target_id,
                    "profile": hub.profiles[client_id],
                    "text": text,
                    "ts": time.time(),
                }
                await hub.send(target_id, {"type": "dm", "message": msg})
                await hub.send(client_id, {"type": "dm", "message": msg})

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
                    hub.blocked[client_id].add(target_id)
                    hub.blocked[target_id].add(client_id)
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
            hub.clients.pop(client_id, None)
            hub.profiles.pop(client_id, None)
            for members in hub.room_members.values():
                members.discard(client_id)
            hub.blocked.pop(client_id, None)
            for s in hub.blocked.values():
                s.discard(client_id)
        await hub.broadcast({"type": "presence", "users": hub.online_snapshot()})
        await hub.broadcast({"type": "rooms", "rooms": hub.room_snapshot()})
