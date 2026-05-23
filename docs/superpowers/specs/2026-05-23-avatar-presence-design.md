# Avatar Presence Placement Design

## Goal

Add an opt-in Appearance setting that moves the active agent avatar from the
small assistant transcript header into a prominent composer-adjacent presence
position. The existing Hermes WebUI appearance and behavior must remain the
default.

## User Requirements

- Desktop and mobile can show a larger avatar adjacent to the message composer.
- The avatar should nearly touch the composer while leaving controls usable.
- The avatar size must match the full initial composer box height.
- The avatar must stay fixed at the initial composer height when the textarea
  grows for longer drafts, especially on mobile.
- Static avatars, reactive avatars, avatar shape, viewed-session profile
  ownership, and profile switching must keep working.
- The feature must not slow profile switching or reactive avatar updates.
- The implementation needs independent frontend visual validation.
- The implementation needs a performance check comparing the current layout and
  the new layout.

## Approved UX Direction

Use the "Composer Presence Rail" layout:

- Add a fixed-size avatar sibling beside `#composerBox`.
- Compact the composer horizontally to make room for the avatar.
- Use a tight but non-overlapping gap between avatar and composer.
- Match the avatar square to the full initial composer box height.
- Keep that avatar size stable while the textarea grows.
- Suppress the small assistant transcript avatar in composer-presence mode so
  the UI has one clear avatar presence signal.
- Preserve current transcript avatar behavior when the setting is off.

The visual target was approved from the corrected Option A mockup shown in the
brainstorm companion on 2026-05-23.

## Setting Contract

Add an Appearance setting named `avatar_presence_layout` with enum values:

- `thread`: current/default behavior.
- `composer`: opt-in composer-adjacent presence avatar behavior.

Server persistence:

- Add the default to `api/config.py`.
- Allow and validate the enum through `/api/settings`.
- Return the value in settings payloads.

Client state:

- Mirror the saved value into localStorage for early layout application.
- Apply `data-avatar-presence="composer"` to the document root only when the
  opt-in mode is active.
- Omit the attribute for default `thread` behavior.

## Rendering Design

The composer avatar must reuse the existing profile avatar rendering and
reactive-state logic rather than adding a second avatar system.

Expected integration points:

- Add composer-adjacent avatar markup near `#composerBox` in `static/index.html`.
- Add a dedicated CSS class such as `.composer-presence-avatar`.
- Extend existing avatar refresh helpers in `static/ui.js` so the new avatar is
  refreshed from the same session-owned profile resolver as message avatars.
- Keep `_conversationProfileAvatarMarkupForState(...)` as the source of profile
  avatar markup for visible chat-owned avatars.
- Ensure `setReactiveAvatarState(...)` updates the composer avatar in the same
  state transitions used by live transcript avatars.

Transcript behavior:

- In `thread` mode, current assistant message header/avatar layout remains
  unchanged.
- In `composer` mode, assistant message headers keep the assistant name and
  metadata but do not show the small role avatar.

## Sizing Design

The avatar size is an initial-composer-size measurement, not a live textarea
measurement.

Implementation should use one low-cost measurement path:

- Measure `#composerBox` once after initial layout, after Appearance/font-size
  changes, and after viewport breakpoint changes.
- Write that measured initial composer height to a CSS variable used by the
  composer avatar's width and height.
- Temporarily measure with the textarea in its collapsed initial state, so long
  draft content does not become the avatar size source.

If browser verification proves fixed CSS variables match the actual initial
composer height across all required font-size and responsive states, the
implementation may replace measurement with those variables. Exact visual match
is the deciding criterion.

Do not update the avatar size on every textarea input event.

The avatar may be recalculated on:

- initial boot,
- Appearance setting changes,
- font-size changes,
- viewport breakpoint changes,
- composer structural changes that alter the initial composer height.

## Performance Constraints

- Do not add avatar-settings network fetches to the hot profile-switch path.
- Do not add per-token DOM or layout work for the composer avatar.
- Reactive state updates should repaint only visible avatar nodes that need the
  new state.
- Avoid `ResizeObserver` on textarea growth unless verification proves a simpler
  approach cannot satisfy exact sizing.
- Preloading behavior should remain limited to existing avatar assets and
  effective reactive slots.

## Testing And Validation

Automated tests should cover:

- `avatar_presence_layout` default, persistence, and enum validation.
- Appearance autosave includes the new setting.
- Default `thread` mode preserves current assistant avatar markup.
- `composer` mode exposes stable DOM hooks for the composer avatar.
- Reactive avatar refresh includes the composer avatar without losing
  session-owned profile identity.
- Long-text composer growth does not imply avatar growth.

Manual and browser validation should cover:

- Desktop, ordinary laptop/narrow, and mobile widths.
- Default mode before/after screenshots showing current behavior preserved.
- Composer mode screenshots showing the avatar adjacent to the composer.
- Initial avatar height exactly matching the initial full composer box.
- Long typed message state where composer grows but avatar stays fixed.
- Profile switching updates the composer avatar and does not flicker.
- Static and reactive avatars render with square and circle shapes.
- Reduced-motion settings still suppress animation but not reactive selection.

Independent validator requirement:

- After implementation, ask a separate frontend/visual validator agent to inspect
  the rendered UI and answer:
  - Does this look right?
  - Is anything visually broken?
  - Does mobile still feel usable?
  - Does the bigger avatar create layout or performance regressions?

Performance validation:

- Compare profile switching in `thread` and `composer` modes.
- Compare reactive avatar state refresh in `thread` and `composer` modes.
- Record whether the new layout changes switch latency or reactive update cost
  enough to be visible or measurable.

## Out Of Scope

- Redesigning the composer controls beyond the required horizontal compacting.
- Changing avatar upload, profile editing, or reactive slot authoring.
- Adding new avatar animation states.
- Making the composer avatar mandatory.
- Adding a frontend framework, build step, or new dependency.
