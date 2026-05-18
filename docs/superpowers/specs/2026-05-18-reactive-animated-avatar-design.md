# Reactive Animated Profile Avatars Design

Date: 2026-05-18
Status: Approved for planning

## Goal

Add complete, opt-in reactive animated avatar support for in-session assistant
messages. Users can keep the current static avatar behavior, or save a
persistent multi-slot animated avatar pack that changes during chat based on
agent activity.

The first version includes a full UI multi-upload editor. Users do not need to
manually place files in the repo or edit JSON.

## Existing Behavior

Profiles currently support one avatar value with these types:

- `emoji`
- `url`
- `asset`
- `image`

The UI can upload PNG, JPEG, GIF, or WebP. Uploaded images are stored as data
URLs in `webui_state/profile_settings.json`, and profile list summaries expose
large uploaded images through `api/profile/avatar-image` so `/api/profiles`
does not embed multi-megabyte data URLs.

Avatars render through `_profileAvatarMarkup()` in `static/ui.js`. Animated
GIF/WebP files can play because they render as normal `<img>` elements, but the
app treats them as one static avatar source. The only current reactive behavior
is a generic CSS pulse on the live assistant avatar while `#liveAssistantTurn`
exists.

## Product Decisions

Reactive playback is chat-only. Profile cards, the profile dropdown, and the
profile hero show a stable idle/static preview rather than changing with live
chat state.

Switching between static and reactive modes is non-destructive:

- Static mode uses the existing static avatar.
- Reactive mode uses the saved animated avatar pack.
- Switching back to static does not delete the reactive pack.
- Switching back to reactive reuses the previously saved pack.
- Clearing the static avatar does not clear the reactive pack.
- Clearing the reactive pack is a separate explicit action.

Users can save an incomplete reactive pack. Missing slots fall back gracefully.

Replacing one reactive slot only replaces that slot. Canceling the dialog
discards unsaved local selections. Saving commits uploaded slot replacements.
After a successful save, old files for replaced or cleared slots are removed.
There is no version history for replaced slot files in the first version.

## Avatar States

The reactive pack supports five slots:

- `idle`: agent is waiting.
- `thinking`: stream is active and reasoning or preparing output.
- `talking`: assistant text is being produced.
- `working`: tools, web searches, shell commands, file edits, or other active
  tool work are in progress.
- `error`: app error, stream error, or failed tool completion.

Future states such as `waiting_user`, `approval`, or tool-specific states can
be added later without changing the first data model.

## Fallback Rules

No state should ever render an empty avatar frame.

Fallback chain:

- `idle`: static avatar, then profile initial.
- `thinking`: `idle`, then static avatar, then profile initial.
- `talking`: `thinking`, then `idle`, then static avatar, then profile initial.
- `working`: `thinking`, then `idle`, then static avatar, then profile initial.
- `error`: `idle`, then static avatar, then profile initial, plus an optional
  error class or ring.

If the browser reports `prefers-reduced-motion: reduce`, chat uses the static
avatar if available. If no static avatar exists, it uses the profile initial.
Animated WebP cannot be reliably paused with CSS, so reduced-motion mode should
avoid animated slots rather than trying to stop playback.

## Data Model

Keep the existing `avatar` and `avatar_shape` fields for static avatars.

Add profile settings fields:

```json
{
  "avatar_mode": "static",
  "reactive_avatar": {
    "version": 1,
    "updated_at": "2026-05-18T00:00:00Z",
    "slots": {
      "idle": {
        "asset_id": "idle-<hash>",
        "filename": "idle.webp",
        "content_type": "image/webp",
        "size": 123456,
        "sha256": "<hex>",
        "animated": true,
        "updated_at": "2026-05-18T00:00:00Z"
      }
    }
  }
}
```

`avatar_mode` defaults to `static` for all existing profiles.

Reactive avatar files are stored as files, not data URLs, under the profile
home:

```text
<profile_home>/webui_state/avatar_assets/<asset_id>.<ext>
```

The settings JSON stores metadata only. File URLs are computed by API responses
so the stored state remains portable and compact.

## Backend API

Keep `/api/profile/settings` compatible with existing static avatar behavior.

Add a new profile-aware endpoint for the full avatar editor:

- `GET /api/profile/avatar-settings?name=<profile>`
- `POST /api/profile/avatar-settings`

`GET` returns:

- static avatar metadata
- avatar shape
- avatar mode
- reactive pack metadata
- computed slot URLs
- effective fallback state for each slot

`POST` accepts `multipart/form-data` with:

- `payload`: JSON describing profile name, selected mode, static avatar updates,
  shape, clear-pack action, clear-slot actions, and which reactive slots are
  expected.
- `slot_idle`, `slot_thinking`, `slot_talking`, `slot_working`, `slot_error`:
  optional file fields for new or replaced slots.

The backend validates all metadata and files before mutating committed state.
It writes uploaded files to a temporary directory, builds the new settings
state in memory, then atomically replaces `profile_settings.json` and moves
files into place. If validation fails, no committed avatar state changes.

Add a route for saved reactive files:

- `GET /api/profile/avatar-asset?name=<profile>&asset=<asset_id>&v=<etag>`

The route only serves metadata-listed assets for that profile, sets the stored
content type, sends `ETag`, and uses private caching.

## Validation And Safety

Allowed uploaded slot types:

- `image/webp`
- `image/gif`
- `image/png`
- `image/jpeg`

WebP is the recommended format for animated slots, but static images remain
valid because users may want still fallbacks.

Validation should not trust only browser-provided MIME types. The backend
checks file signatures:

- WebP: `RIFF....WEBP`
- GIF: `GIF87a` or `GIF89a`
- PNG: PNG signature
- JPEG: JPEG SOI marker

For WebP, the backend can detect animation by scanning valid chunks for `ANIM`
or `ANMF`. Animated detection is informative, not required for acceptance.

Recommended limits:

- 5 MiB per slot
- 20 MiB per reactive pack
- 5 slots maximum

Reject paths or asset IDs from clients. The server generates `asset_id` values
from slot names plus content hashes.

## Frontend UI

The existing Change avatar dialog gains two top-level modes:

- Static avatar
- Reactive animated avatar

Static mode keeps the current emoji, upload, URL, asset, and shape controls.

Reactive mode shows five slot rows:

- Idle
- Thinking
- Talking
- Working
- Error

Each slot row includes:

- current preview
- upload or replace control
- clear button
- filename
- MIME and size
- animation/static badge when known
- fallback note when missing

The dialog includes explicit actions:

- Save avatar
- Cancel
- Clear static avatar
- Clear reactive pack

Mode selection is a saved setting. Selecting static mode and saving does not
remove reactive slot files. Selecting reactive mode and saving activates the
pack even if some slots are missing.

The UI should preload active chat slot URLs after:

- boot active profile load
- profile switch
- avatar settings save
- session pane activation when the session profile differs from the active
  topbar profile

## Runtime State Controller

Add a small frontend avatar runtime controller instead of spreading state logic
through all render helpers.

Responsibilities:

- hold active profile avatar mode, static avatar, shape, and reactive pack
- choose the effective asset for a requested state using fallback rules
- preload slot images
- update live assistant avatar nodes in chat
- honor `prefers-reduced-motion`
- debounce transitions to prevent flicker and constant WebP restarts

Recommended frontend boundary:

- Add a new `static/avatar.js` file for avatar state and rendering helpers.
- Keep existing global wrappers where needed for compatibility with current
  `ui.js`, `panels.js`, and `boot.js`.
- Have `messages.js` emit coarse activity updates to the controller.

Initial stream mapping:

- stream start: `thinking`
- `reasoning`: `thinking`
- `token` or `interim_assistant`: `talking`
- `tool`: `working`
- active tool count greater than zero: keep `working`
- `tool_complete` with `is_error`: temporary `error`
- `apperror` or terminal stream error: `error`
- `done` or `cancel`: `idle`

State debounce:

- Do not switch more often than every 150 ms.
- Keep `error` visible for about 2500 ms before returning to the next effective
  state.
- Keep `talking` active while tokens are arriving, then decay to `thinking` if
  the stream is still active.

## Compatibility Fixes Included

The implementation should fix the current message avatar shape inconsistency:
`_assistantRoleHtml()` should pass `window._activeProfileAvatarShape` when
rendering new assistant role icons.

The implementation should add image load failure fallback for avatar `<img>`
nodes so broken URLs or missing assets do not leave empty frames.

## Testing Plan

Backend tests:

- default `avatar_mode` is static for legacy profiles
- reactive pack metadata round-trips
- incomplete packs are accepted
- static/reactive mode switches preserve the inactive mode data
- replacing one slot preserves other slots
- clearing static avatar does not clear reactive pack
- clearing reactive pack does not clear static avatar
- multipart save is atomic on validation failure
- oversize files are rejected
- MIME spoofing is rejected by signature checks
- avatar asset route only serves metadata-listed files for the profile
- ETag and cache headers are sent

Frontend/static tests:

- dialog renders static and reactive modes
- five reactive slots are present
- save path builds `FormData` with payload plus changed slot files
- fallback labels are shown for missing slots
- static mode save does not request pack deletion
- reactive clear-pack action is explicit
- `_assistantRoleHtml()` passes avatar shape
- reduced-motion branch avoids animated slots

Browser/manual tests:

- upload a full WebP pack and verify idle, thinking, talking, working, and error
  transitions in chat
- upload only idle and talking and verify fallback behavior
- switch to static, save, reload, switch back to reactive, and verify slots are
  still available
- replace one slot and verify the other saved slots remain
- trigger a broken URL or deleted asset and verify fallback rendering
- verify profile cards/dropdown/hero use idle/static preview and do not react to
  chat state
- verify responsive dialog layout on desktop and mobile
- verify reduced-motion behavior

## Documentation And Changelog

Update `CHANGELOG.md` because this is user-visible behavior.

Update `TESTING.md` with manual validation steps for reactive avatars and
reduced-motion behavior.

Update profile/avatar UI documentation only if the repo has an existing user
guide section that already describes avatar editing.

## Non-Goals

The first version does not add:

- multiple named avatar packs per profile
- version history for replaced slot files
- per-tool custom animation slots
- lip-sync, audio-reactive animation, or frame-level control
- server-side image transcoding
- extracting first-frame posters from animated files

Those can be layered on after the persistent pack model and runtime state
controller are stable.
