# Photo Sharing — Design Spec

## Research Findings

### Google Photos Library API — DEAD END
- `readonly.appcreateddata` scope can see album metadata but returns **empty results** when listing items manually added by users
- Shared album APIs fully deprecated (March 2025)
- Library API can only read items the app itself created — useless for user-curated albums
- **Album auto-sync was built and removed** — the API simply doesn't work for this use case

### Google Photos Picker API (current, working)
- Manual selection each time, no sync, no albums
- Works reliably for selecting specific photos

### Bottom line
Google can't solve family sharing or auto-sync. The upload endpoint is the path forward.

## Working Features

### Upload Endpoint (family sharing) — SHIPPED
**Solves:** Multiple family members contributing photos to the frame
**How:**
- `/upload` page on the Pi — mobile-friendly, scan QR to open
- Pick photos from camera roll (works with iCloud, Google Photos, any phone)
- Photos upload directly to Pi, appear on frame
- No sign-in, no Google account needed
- Token-protected URL shared via QR code or link

**UX flow:**
1. Family member scans QR on the frame (or gets link texted to them)
2. Opens upload page on their phone
3. Taps "Select Photos" → camera roll picker opens
4. Selects photos → uploads → "Added to frame!" confirmation
5. Frame shows the new photos

### Google Photos Picker — SHIPPED
- Owner signs in with Google, selects specific photos
- Photos download to the Pi
- Works via admin panel "Pick New Photos" button

## Future: Master/Child Pi Sync
See Phase 2 planning for Pi-to-Pi sync (master broadcasts photos to child frames over HTTP).
