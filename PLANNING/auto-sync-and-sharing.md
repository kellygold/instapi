# Photo Sharing & Auto-Sync — Design Spec

## Research Findings

### Google Photos Library API
- Can create an album per user, list its contents, auto-sync periodically
- **Shared album APIs fully deprecated** (March 2025) — no way for multiple users to contribute to one album via API
- Only reads items the app created or that are in app-created albums
- Useful for **single-user auto-sync** (owner's own photos)

### Google Photos Picker API (current)
- Manual selection each time, no sync, no albums
- Works but tedious for repeat use

### Bottom line
Google can't solve family sharing. The API literally doesn't support it anymore.

## Two Features to Build

### Feature 1: Upload Endpoint (family sharing)
**Solves:** Multiple family members contributing photos to the frame
**How:**
- `/upload` page on the Pi — mobile-friendly, scan QR to open
- Pick photos from camera roll (works with iCloud, Google Photos, any phone)
- Photos upload directly to Pi, appear on frame
- No sign-in, no Google account needed
- Shareable QR code on the frame links to the upload page

**UX flow:**
1. Family member scans QR on the frame (or gets link texted to them)
2. Opens upload page on their phone
3. Taps "Select Photos" → camera roll picker opens
4. Selects photos → uploads → "Added to frame!" confirmation
5. Frame shows the new photos

### Feature 2: Google Photos Album Sync (owner auto-sync)
**Solves:** Frame owner's photos auto-updating without manual picker sessions
**How:**
- Owner signs in once with Library API scopes
- InstaPi creates an "InstaPi" album in their Google Photos
- Pi polls the album every 30 min (or configurable)
- New photos auto-download, deleted photos auto-remove
- Requires token refresh (offline access with refresh token)

**UX flow:**
1. Owner enables "Auto-Sync" in admin settings
2. Signs in with Google (one time)
3. "InstaPi" album appears in their Google Photos
4. Owner drops photos into that album from their phone
5. Frame updates automatically

## Priority
1. **Google Photos album sync** — build for owner's auto-sync first
2. **Test shared album behavior** — does the API return photos added by other users to an app-created shared album? If yes, family sharing is solved through Google Photos natively.
3. **Upload endpoint (fallback/complement)** — if shared album reading doesn't work, or for iCloud users, or as a complement for anyone without Google Photos

## Upload Endpoint Detail

### How it works on the phone
A web page with `<input type="file" accept="image/*" multiple>` — when tapped on a phone, the OS opens the native camera roll picker (iOS Photos app, Android gallery). User selects photos, browser uploads them as multipart form data to the Pi. No app install, no sign-in, no Google account needed.

### Accessible via
- **Local network:** `http://instapi.local:3000/upload`
- **Remote:** `https://instapi.ngrok.dev/upload`
- **QR code:** on the frame's slideshow overlay (like the existing admin QR)

### UX
1. Open URL or scan QR → sees simple "Add photos to [Frame Name]" page
2. Tap "Select Photos" → native camera roll picker opens
3. Pick 1 or many photos → upload progress shows
4. "Photos added!" confirmation → photos appear on frame

### Technical
- `POST /upload` accepts multipart `image/*` files
- Saves to `static/photos/upload/` subdirectory
- Generates thumbnails (reuse `Image.thumbnail()` from utils.py)
- Updates `device_state["photo_urls"]`
- USB mode: auto-syncs to USB after upload (calls `sync_photos_to_usb()`)
- Max 10MB per file, basic validation (is it actually an image?)
- No auth — anyone with the URL can upload (frame is meant to be shared)
- Mobile-friendly page matching admin design (dark theme, green accents)

## Implementation Plan: Google Photos Album Sync

### New scopes needed
- `photoslibrary.readonly` — read album contents
- `photoslibrary.appendonly` — create album
- `photoslibrary.edit.appcreateddata` — manage the app-created album

### Flow
1. Owner enables "Auto-Sync" in admin settings
2. Signs in with Library API scopes (one-time, with offline refresh token)
3. InstaPi creates an "InstaPi Frame" album in their Google Photos (or reconnects to existing one)
4. Album ID stored in device_state
5. Cron/background task polls album every 30 min
6. New photos downloaded, removed photos cleaned up
7. Refresh token used to maintain access without re-auth

### Key implementation details
- Store refresh token in device_state (persisted to disk — needed for long-term access)
- Need `access_type="offline"` on auth flow to get refresh token
- Track which photos are already downloaded (by media item ID) to avoid re-downloading
- Handle token refresh when access token expires (~1 hour)
- Album sync runs as background task (similar to existing poll_for_media_items pattern)

### Files to modify/create
- `app/config.py` — add Library API scopes
- `app/routes/admin_routes.py` — add auto-sync toggle + settings UI
- `app/utils.py` — add album sync logic (create album, list items, download new, remove deleted)
- `app/templates/admin.html` — auto-sync settings section
- `app/main.py` — start background sync timer on startup if enabled

### Testing the shared album hypothesis
After building basic album sync:
1. Create the album via API
2. Share it manually in Google Photos with another account
3. Other account adds photos to it
4. Check if `mediaItems.search` with the album ID returns the shared photos
5. If yes → family sharing works natively
6. If no → build upload endpoint as fallback
