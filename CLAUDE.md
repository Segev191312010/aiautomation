# CLAUDE.md - Project Rules & Workflow

## GOD RULE

**NEVER commit or push anything without explicit user approval.** No exceptions. Always ask first.

---

## Development Method: Relay Race

We develop in a **relay race method**:

1. **Master session** plans features in a waterfall method
2. For each stage, create a **session prompt** using `@session-prompt-generator` to paste into a new isolated session
3. Each isolated session does the work on its designated branch
4. At the end of each stage, create a **handoff note** using `@handoff-generator`

---

## Handoff Document Generator

**Single-use prompt. Copy, fill in, paste into NEW Claude conversation, get output, close.**

### How to Use

1. Copy the prompt below (START to END)
2. Fill in YOUR information about what you built
3. Paste into NEW Claude conversation
4. Get your handoff document
5. Save to `handoffs/` folder
6. Close conversation (done)

### THE PROMPT (COPY FROM HERE)

```
Generate a handoff document for the work I just completed.

DATE: [Today - e.g., 2025-01-03]
SESSION: [Type - e.g., backend-endpoint, frontend-component]
FEATURE: [What you built - e.g., "Video upload API"]

WHAT I BUILT:
[Summary in 2-3 sentences]

FILES:
- [File 1 - e.g., "app/api/videos/upload/route.ts (created)"]
- [File 2 - e.g., "lib/supabase-storage.ts (modified)"]

API/INTERFACE:
[If you built an API or interface, describe it]

TESTED:
- [Test 1 - e.g., "✅ 45-second video uploads successfully"]
- [Test 2 - e.g., "❌ Not tested on iOS yet"]

NOT DONE:
- [Todo 1 - e.g., "Video compression"]
- [Todo 2 - e.g., "Duplicate detection"]

QUESTIONS:
- [Question 1 - e.g., "Should we compress client-side or server-side?"]

NEXT:
[What to work on next]

---

Generate complete handoff document with:
- Proper markdown formatting
- All standard sections
- Code examples where relevant
- Clear and actionable
- Filename: handoffs/[date]-[brief-description].md

Make it ready to copy-paste and save immediately.
```

### (COPY TO HERE)

### Handoff Document Format

Every handoff document must include these sections:

```markdown
# HANDOFF: [What Was Done] → [What's Next]

**Date:** YYYY-MM-DD
**Session:** [Type]
**Status:** ✅ Complete | 🔄 Partial | ❌ Blocked

## What We Built
[2-3 sentence summary]

## Files Created/Modified
- [file list with (created) or (modified)]

## API Specification
[If applicable - endpoint, request, response]

## Testing Completed
- ✅ [what passed]
- ❌ [what wasn't tested]

## What's NOT Done (Future Work)
- [ ] [actionable items]

## Open Questions
- [ ] [decisions needed]

## Next Session Should Be
[Specific tasks + files to modify]
```

### Example Input

```
SESSION INFORMATION:
Date: 2025-01-03
Session Type: backend-endpoint
Feature/Component: Video Upload API

WHAT I BUILT:
Built complete video upload endpoint. Receives video file from frontend, validates format (mp4/webm only), size (max 100MB), and duration (max 60 seconds). Uploads to Supabase storage bucket with UUID filename. Saves metadata to video_uploads table including file path, size, duration, mime type. Returns videoId and storage URL to frontend. All validation working, error handling implemented.

FILES CREATED/MODIFIED:
- app/api/videos/upload/route.ts (created - main upload handler)
- lib/supabase-storage.ts (created - storage helper functions)
- db/migrations/003_video_uploads.sql (created - video_uploads table)
- types/api.types.ts (modified - added UploadResponse type)

DATA CONTRACTS/APIS:
POST /api/videos/upload
Request: FormData with video (File), userId (string), duration (string), timestamp (string)
Success Response: { success: true, videoId: string, storageUrl: string, readyForAnalysis: false }
Errors: 413 file too large, 400 invalid format or validation failed, 500 server error

Database table: video_uploads (id, user_id, file_path, storage_url, duration, file_size, mime_type, upload_timestamp, status, created_at)

TESTING DONE:
Manually tested: 45-second .webm video (22MB) uploaded successfully, 120MB file correctly rejected with 413, .mov file rejected with 400, missing userId returns 400. Database entries created correctly. Supabase storage working. Haven't tested duplicate uploads or concurrent uploads yet.

WHAT'S NOT DONE:
- Hume AI analysis trigger (that's a separate session)
- Upload progress tracking
- Video compression
- Duplicate detection
- Retry logic for failed uploads

OPEN QUESTIONS:
- Should we add video compression before upload, or handle server-side?
- Do we need to generate a thumbnail on upload?
- What happens if Supabase storage quota is reached?

NEXT SESSION:
Integration session - connect frontend VideoRecorder component to this backend endpoint. Implement uploadVideo() function, handle loading states, show upload progress, display errors to user. Test complete flow end-to-end.
```

### Example Output

```markdown
# HANDOFF: Video Upload Backend API → Frontend Integration

**Date:** 2025-01-03
**Session:** Backend - Video Upload Endpoint
**Status:** ✅ Complete

## What We Built

Complete video upload API endpoint with validation, Supabase storage integration, and database metadata storage. Accepts video files from frontend, validates format/size/duration, uploads to cloud storage, and returns tracking information.

## Files Created/Modified

- `app/api/videos/upload/route.ts` - Main upload handler (new)
- `lib/supabase-storage.ts` - Storage helper functions (new)
- `db/migrations/003_video_uploads.sql` - Database schema (new)
- `types/api.types.ts` - Added UploadResponse type (modified)

## API Specification

**Endpoint:** `POST /api/videos/upload`

**Authentication:** Required (user session)

**Request:** FormData containing:
- `video` (File): Video file blob
- `userId` (string): User UUID
- `duration` (string): Video duration in seconds
- `timestamp` (string): Recording timestamp

**Success Response (200):**

    {
      "success": true,
      "videoId": "uuid-here",
      "storageUrl": "https://[project].supabase.co/storage/v1/object/public/user-videos/uuid.webm",
      "readyForAnalysis": false
    }

**Error Responses:**
- `413 Payload Too Large` - File exceeds 100MB
- `400 Bad Request` - Invalid format (not mp4/webm), validation failed, or missing fields
- `500 Internal Server Error` - Storage or database failure

## Validation Rules

- **File size:** Max 100MB
- **Format:** `.mp4` or `.webm` only
- **Duration:** Max 60 seconds
- **User authorization:** Request userId must match authenticated user

## Database Schema

    CREATE TABLE video_uploads (
      id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
      user_id UUID NOT NULL REFERENCES users(id),
      file_path TEXT NOT NULL,
      storage_url TEXT NOT NULL,
      duration DECIMAL NOT NULL,
      file_size BIGINT NOT NULL,
      mime_type VARCHAR(50) NOT NULL,
      upload_timestamp BIGINT NOT NULL,
      status VARCHAR(20) DEFAULT 'uploaded',
      created_at TIMESTAMP DEFAULT NOW()
    );

## Storage Details

- **Bucket:** `user-videos` (public read access)
- **File naming:** `{userId}/{uuid}.{ext}`
- **Path structure:** Organized by user for easy cleanup

## Integration Notes for Frontend

**How to call:**

    const formData = new FormData();
    formData.append('video', videoBlob, 'recording.webm');
    formData.append('userId', currentUser.id);
    formData.append('duration', duration.toString());
    formData.append('timestamp', Date.now().toString());

    const response = await fetch('/api/videos/upload', {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      if (response.status === 413) {
        // Handle file too large
      } else if (response.status === 400) {
        // Handle validation error
      }
    }

    const data = await response.json();
    // Use data.videoId for tracking

## Testing Completed

- ✅ Valid upload (45-second .webm, 22MB)
- ✅ Oversized file rejection (120MB → 413)
- ✅ Invalid format rejection (.mov → 400)
- ✅ Missing userId validation (→ 400)
- ✅ Database entry creation verified
- ✅ Supabase storage functional
- ❌ Not tested: duplicate uploads
- ❌ Not tested: concurrent uploads
- ❌ Not tested: iOS Safari compatibility

## What's NOT Done (Future Work)

- [ ] Hume AI analysis trigger (next session)
- [ ] Upload progress tracking
- [ ] Video compression
- [ ] Duplicate upload detection
- [ ] Retry logic for failed uploads

## Open Questions

- [ ] Should we add client-side video compression before upload?
- [ ] Do we need thumbnail generation on upload?
- [ ] What's the fallback if Supabase storage quota is reached?

## Next Session Should Be

**Integration Session:** Connect frontend VideoRecorder component to this backend endpoint.

**Tasks:**
1. Implement `uploadVideo()` function in `hooks/useVideoUpload.ts`
2. Add loading state during upload
3. Show upload progress (if possible)
4. Display error messages to user (file too large, invalid format, etc.)
5. Show success confirmation
6. Test complete recording → upload → success flow

**Files to modify:**
- `hooks/useVideoUpload.ts` (main implementation)
- `components/VideoRecorder.tsx` (wire up the hook)
- `components/UploadStatus.tsx` (create for feedback)
```

### Quick Mode

If you just want a minimal handoff without all the details:

```
Generate a brief handoff document:

Built: [one sentence]
Files: [list]
What's next: [one sentence]

[Paste your info here]
```

### Tips for Better Handoffs

1. **Be specific about what works** - "Upload endpoint handles 100MB files correctly" not just "Upload works"
2. **Document your testing** - Future you needs to know what was actually tested
3. **List open questions** - Capture things you're unsure about while they're fresh
4. **Make "next session" actionable** - Specific enough that anyone can start immediately
5. **Include code examples** - Show exactly how to use what you built

### Automation Tip

You can make this even faster by keeping notes during your session:

```markdown
# Session Notes (scratch pad)

✅ Created upload endpoint
✅ Added validation for file size
✅ Tested with 45-second video
❌ Didn't implement compression
? Should we compress client-side?

Files touched:
- route.ts
- types.ts

Next: Wire up frontend
```

Then paste these notes into the handoff generator and it'll structure them properly.

---

## Session Prompt Generator

**Single-use, disposable prompt generator. Copy, fill in, paste into NEW Claude conversation, get output, close.**

### How to Use

1. Copy the prompt below (from START to END)
2. Fill in YOUR information in the brackets [like this]
3. Paste entire thing into a NEW Claude conversation
4. Get your session prompt
5. Close that conversation (you're done with it)
6. Use the generated prompt in your work session

### THE PROMPT (COPY FROM HERE)

```
Generate a focused development session prompt.

PROJECT: [Your project name - e.g., "Evoke Labs Platform"]
TECH: [Your stack - e.g., "React, Next.js, Supabase, Hume AI"]

BUILDING: [Specific feature/component - e.g., "Video upload API endpoint that validates and stores videos"]

SESSION TYPE: [Pick one: frontend-component, backend-endpoint, integration, external-service, database-schema, refactoring]

ALREADY DONE:
- [Thing 1 that already exists - e.g., "Frontend VideoRecorder outputs Blob"]
- [Thing 2 that already exists - e.g., "Supabase storage bucket configured"]
- [Thing 3 - or delete this line if not needed]

REQUIREMENTS:
- [Requirement 1 - e.g., "Max file size 100MB"]
- [Requirement 2 - e.g., "Only mp4/webm formats"]
- [Requirement 3 - or delete this line]

---

Generate a session start prompt with:
- Clear SCOPE (what we're building)
- Explicit OUT OF SCOPE (what to ignore)
- Minimal CONTEXT (just enough)
- Specific FILES to focus on
- Clear CURRENT TASK
- Appropriate boundaries

Format as ready-to-paste prompt for immediate use.
```

### (COPY TO HERE)

### Example Usage

**Input to Generator:**

```
PROJECT CONTEXT:
Project Name: Evoke Labs Platform
Tech Stack: React + TypeScript, Next.js, Supabase, Hume AI
Current Phase: MVP, video upload flow

ARCHITECTURE SUMMARY:
Users record vertical videos (TikTok-style), upload them, we analyze emotions with Hume AI, show results on dashboard.

WHAT I WANT TO BUILD:
Backend API endpoint for video upload. Receives video file, validates format and size, uploads to Supabase storage, saves metadata to database.

SESSION TYPE:
backend-endpoint

DEPENDENCIES/CONTEXT:
- Frontend VideoRecorder component done (outputs Blob, duration, timestamp)
- Supabase storage bucket "user-videos" exists
- Database has "users" table
- Need to create "video_uploads" table

SPECIFIC CONCERNS:
- Max 100MB file size
- Only mp4 or webm formats
- Must validate video duration < 60 seconds
- Need to return videoId to frontend for tracking
```

**Output from Generator:**

```
We're working on the Evoke Labs emotion analysis platform. This is a BACKEND-ONLY session.

SCOPE OF THIS SESSION:
- Video upload API endpoint (POST /api/videos/upload)
- File validation (size, format, duration)
- Supabase storage integration
- Database metadata storage (video_uploads table)
- Response with videoId and storage URL

OUT OF SCOPE (don't worry about these):
- Frontend upload UI (already built)
- Hume AI analysis (separate session)
- Results display
- User authentication (already working)
- Video compression/processing

CONTEXT YOU NEED:
- Frontend sends FormData with: videoBlob (File), userId (string), duration (number), timestamp (number)
- Videos store in Supabase bucket: "user-videos"
- Max file size: 100MB
- Accepted formats: .mp4, .webm only
- Max duration: 60 seconds
- Need to return: videoId, storageUrl, status

FILES TO FOCUS ON:
- app/api/videos/upload/route.ts (create new)
- lib/supabase.ts (existing helper)
- types/video.types.ts (add types if needed)

CURRENT TASK:
Build the POST /api/videos/upload endpoint that:
1. Validates video file (format, size, duration)
2. Uploads to Supabase storage with proper naming
3. Creates database entry in video_uploads table with metadata
4. Returns success response with videoId and storageUrl
5. Handles errors appropriately (413 for too large, 400 for invalid format, 500 for failures)

Let's focus ONLY on making this endpoint work. Don't suggest frontend changes or analysis features - those are separate sessions.
```

### Tips for Better Prompts

1. **Be specific about what you want to build** - "video upload endpoint" is better than "backend stuff"
2. **Mention what's already done** - Helps Claude understand what NOT to rebuild
3. **State constraints clearly** - File size limits, format requirements, etc.
4. **Choose the right session type** - Frontend/backend/integration have different needs
5. **Keep it focused** - If you find yourself listing 5+ different things, break it into multiple sessions

### Session Types Reference

| Type | Use For |
|------|---------|
| **frontend-component** | Building UI components, React/TypeScript work, user interactions, client-side validation |
| **backend-endpoint** | API routes, database operations, server-side validation, external API calls |
| **integration** | Connecting frontend to backend, wire up API calls, error handling, end-to-end testing |
| **external-service** | Third-party API integration (Stripe, Hume, etc.), service wrappers, auth with services |
| **database-schema** | Table design, migrations, indexes, relationships |
| **refactoring** | Code cleanup, performance optimization, restructuring without adding features, tech debt |

---

## Project Info

- **Name:** TradeBot Dashboard (`trading-dashboard` v2.0.0)
- **Stack:** React 18, TypeScript 5.5, Vite 5.4, Zustand 4.5, Tailwind 3.4, lightweight-charts 4.2
- **Backend:** FastAPI (expected at `:8000`, not in this repo)
- **Handoffs directory:** `handoffs/`
