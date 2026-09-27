# Recadoodle
## UNSTABLE BRANCH IS FOR UPDATES THAT WE ARE TESTING. IT MIGHT NOT ALWAYS BE "UNSTABLE"
An experimental, unofficial Python/Flask replacement backend for preserved April 2023
Rec Room clients (`20230414`). Not affiliated with or endorsed by Rec Room Inc.
Compatibility with other builds is not guaranteed.

## important read me : 
just before you begin the deployment process please keep in mind this server is not meant to be used for communities as it lacks security and is intended for experimentation.

[Join the Recadoodle Discord](https://discord.gg/HjcpsaGnB6) for news, updates, and help.


## Features

- Password accounts, developer roles, token authentication and a password-required account picker.
- Personal dorms, room discovery, room saves, circuit values and room thumbnails.
- Avatar/equipment catalogs, outfits, friends, chat and WebSocket notifications.
- A built-in image server: authenticated JPEG, PNG, and WebP uploads, on-the-fly resizing
  and square cropping, saved player photos, and profile photos with stable public URLs.
- Persistent game-completion token rewards and atomic token-store purchases.
- Experimental clubs, events, reports and other protocol endpoints; some remain stubs.
- Database readiness monitoring at `/readyz`, with HTTP 503 when the database is unavailable.
- Server details at `/api/server-info`: package version, supported client build, UTC time, and uptime.
- A live `/status` dashboard with player, room, club and event activity, recent rooms,
  upcoming events and automatic refresh. The same public data is available as JSON at
  `/api/server-stats`.

The server-info endpoint works without login or a database connection. Uptime is measured
from application startup and resets on restart. The version is `unknown` if the package
has not been installed. Use `/readyz` to check database readiness.

Upload a profile photo as multipart form data to `/account/me/profilephoto`. The response
includes its public `/account/{accountId}/profilephoto` URL. Photos default to a 5 MB limit,
configurable with `PROFILE_PHOTO_MAX_BYTES`.

## Image server

Images are served by the `Images` service (`img.` subdomain, or the main host in
`SINGLE_HOST_MODE`) at `/{imageName}`. Room thumbnails such as `/RecCenter.jpg` are bundled;
unknown names return a generated placeholder so the client never shows a broken image.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/images/v1/upload` | Upload an image (multipart, any field name). Returns its name, URL, size and dimensions. |
| `POST` | `/api/images/v4/uploadsaved` | Save a photo to the player's gallery: multipart image plus optional `RoomId` and `Caption`/`Description`, or JSON `{"ImageName": ...}` for an existing image. |
| `GET` | `/{imageName}` | Download an image. |
| `GET` | `/api/images/v1/metadata/{imageName}` | Owner, type, dimensions, size and SHA-256 of an uploaded image. |
| `GET` | `/api/images/v1/{id}` | One saved photo. |
| `GET` | `/api/images/v4/player/{accountId}` | A player's saved photos, newest first. |
| `GET` | `/api/images/v1/room/{roomId}` | Photos taken in a room, newest first. |
| `GET` | `/api/images/v5/bulk?ids=1,2` | Several saved photos by ID. |
| `DELETE` | `/api/images/v1/{id}` | Delete a saved photo (owner, moderator or developer). The file is removed once nothing references it. |

Every image URL accepts `width`, `height` and `cropSquare=true`, for example
`/{imageName}?width=256&cropSquare=true`. Images keep their aspect ratio, fit inside both
`width` and `height` when both are given, and are never enlarged. Resized copies are kept
in a per-process memory cache bounded by `IMAGE_VARIANT_CACHE_BYTES`.

Uploads are decoded to reject anything that is not a real JPEG, PNG, or WebP image, turned
upright according to their EXIF orientation, and re-encoded so EXIF data (including GPS
location and camera details) is removed. Only the colour profile is kept. Animated images keep
their first frame only. `/upload` with `FileType=3` stores valid images the same way; other
data is kept as an opaque blob, as before.

| Setting | Default | Purpose |
| --- | --- | --- |
| `IMAGE_MAX_BYTES` | 10 MB | Largest accepted image upload. |
| `IMAGE_MAX_PIXELS` | 40,000,000 | Largest accepted width × height, guarding against decompression bombs. |
| `IMAGE_MAX_DIMENSION` | 4096 | Largest `width`/`height` a request may ask for. |
| `IMAGE_VARIANT_CACHE_BYTES` | 64 MB | Memory for resized copies. |

Uploaded images live under `instance/uploads/image/`; include that folder in backups.

These endpoints do not guarantee every feature works in-game. This is not a hardened,
production-ready public service. Use a small, controlled test deployment first.

## Quick start (Windows, local testing)

Install Python 3.12. In this folder:

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe manage.py create-developer --username YOUR_USERNAME
.\.venv\Scripts\python.exe serve.py
```

The password is entered privately at the prompt. Coach occupies ID 1; the first developer
on an empty database normally receives ID 2. No SQLite account or cloud database signup
is required. SQLite stores local data under `instance/`.

Create a verified online backup at any time with:

```powershell
.\.venv\Scripts\python.exe manage.py backup-database
```

Visit `http://localhost:5000/healthz` to check the API, or `http://localhost:5000/status`
for the live browser dashboard. `serve.py` is a local development
server, not the recommended public deployment. See [DEPLOYMENT.md](DEPLOYMENT.md) for
Docker, Cloudflare Tunnel, Photon settings, client setup and troubleshooting.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Privacy and security

Never upload `.env`, `instance/`, client logs, local patch configs or backups. The Git and
Docker ignore files exclude these. Use a unique JWT secret, strong passwords and HTTPS.
No default developer credentials are provided. Keep passwordless login disabled.
The cached-platform association only selects an account; it does not authenticate it.
Client debug HTTP logging can expose passwords and tokens: leave it off.

For an isolated local test server only, setting both
`ALLOW_PASSWORDLESS_ACCOUNTS=true` and `CREATE_DEVELOPER_ACCOUNTS_ON_LOGIN=true`
makes the in-game Create Account flow create developer/moderator accounts. Never enable
these options on a public deployment: anyone who can reach it could gain developer access.

WebSocket connection state and rate limits are process-local, so the supplied deployment
uses one worker. Do not increase replicas/workers without shared state support.

Game rewards grant `GAME_REWARD_TOKENS` once per game-session identifier and stop at
`DAILY_GAME_REWARD_LIMIT` per account each UTC day. Store purchases use archived prices,
reject stale client prices, and never allow a token balance to become negative.

## Attribution

Required third-party MIT notices are retained in `LICENSE` and
`rrserver/data/THIRD-PARTY-LICENSE`. Catalog identifiers and game-related
assets do not imply ownership of Rec Room trademarks or a license to distribute its client.
No client patch, reference checkout, game executable, generated interop assembly, or private server dataset is included.
