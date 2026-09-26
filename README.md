# No Limit Chat MVP v0.1.1

Functional first version of an anonymous, Hebrew-first real-time chat product inspired by the interaction model of Catch Chat, with an original UI.

## Included
- Anonymous 18+ entry: nickname, age, gender, interested-in, region, relationship status.
- Local device profile memory (optional).
- Real-time WebSocket presence.
- Online user list with free sorting: women first, men first, age, newest, nickname.
- User search.
- Public rooms and user-created rooms.
- Private/password rooms.
- Room access filters: gender, age range, region, relationship status.
- Room chat and direct messages.
- DND mode.
- DM policy: everyone, women only, men only, nobody.
- Ignore (local/silent), block (bilateral), report.
- Responsive mobile UI with three tabs.
- PWA manifest/service worker.
- Messages are ephemeral/in-memory; room metadata and reports persist in SQLite.

## Current staging baseline
This repository is the first deployable baseline for No Limit Chat. It is intentionally being put online early so v1 can be built and tested continuously rather than waiting for every v1 feature to be finished first.

## v1 roadmap already agreed
- Persistent internal inbox / offline messages, unread badges and read receipts.
- Typing indicators.
- Richer profiles and longer descriptions.
- Multiple profile photos with per-photo public/private visibility and selective reveal.
- Friend requests and persistent relationships.
- Public-room image posting.
- Rich link previews / embeds, including YouTube.
- Structured contact-card sharing (phone, WhatsApp, Telegram, email, website, etc.).
- Advanced filters, exposure worlds and visibility controls, including Kinky and Soteh as distinct self-identification worlds.
- Built-in Morin AI assistant.
- Age-banded youth-safe No Shame area with stricter capabilities, text-first design and safeguarding.
- Voice/video can be added when its moderation and storage model is ready.
- Production moderation/admin tooling, persistent accounts/verification and managed database.

v2 is reserved for user-requested enhancements and later ideas after v1 ships.

## Run
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
Then open `http://127.0.0.1:8000`.

## Production notes
Before public launch, add TLS, reverse proxy, rate limiting, abuse prevention, persistent identity/account option, moderation workflow, privacy/terms pages, backups, and a production database.

## Persistent storage and backups
Set `CHAT_DATA_DIR` to a mounted persistent directory before accepting real user data. The application stores `chat.db` and `uploads/` together there. If unset, both are stored next to the application code for local testing.

Run `python backup_data.py --data-dir /path/to/persistent-data --output /safe/offsite/backup.zip` to create a consistent SQLite snapshot and archive its photos. Backups contain private messages and images; store them securely outside the application server. To restore while the service is stopped, extract `chat.db` and `uploads/` into an empty `CHAT_DATA_DIR` and restart. Test restoration before relying on backups.


## Render free deployment
- Service type: Web Service
- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`
- Free Render filesystem is ephemeral. It is suitable only for disposable testing. Set `CHAT_DATA_DIR` to durable storage before real users join; otherwise accounts, messages, photos, rooms and reports can disappear on redeploy or restart.


## One-click staging deploy
[Deploy this repository on Render](https://render.com/deploy?repo=https://github.com/lolaweiss73-coder/NoLimit-Chat)
