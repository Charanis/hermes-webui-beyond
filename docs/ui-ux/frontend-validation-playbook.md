# Frontend Validation Playbook

Use this checklist for UI and UX changes after reading `CONTRIBUTING.md` and
`TESTING.md`. Keep it compact, publishable, and focused on lessons that should
guide future contributors.

## Before Mocking or Building

- Identify existing controls, layout patterns, and state APIs before designing
  new ones.
- If a user names an existing picker, modal, tile, or control, reuse it or write
  down why it cannot be reused before creating another version.
- Mocks should use the app's real visual language: existing classes, spacing,
  labels, control shapes, and interaction patterns wherever possible.
- Treat mocks as product direction, not permission to bypass established UI
  building blocks.

## State Boundaries

- Keep selected item, active/default item, chat target, and live runtime status
  as separate concepts unless the product behavior explicitly links them.
- Do not make navigation or chat launch perform heavyweight default-setting
  operations.
- Bind visible controls before optional async hydration so the panel is usable
  while deeper data loads.
- Lazy-load modal-only or detail-only settings. Summary tiles should not require
  fetching every nested configuration.

## Performance Checks

- On a fresh app start, measure how quickly the affected panel becomes usable.
- Check for redundant API calls, all-profile scans, embedded large payloads, and
  synchronous sidebar refreshes in interaction-critical paths.
- Prefer cheap summary metadata for list rows and tiles; scan profiles,
  workspaces, or skills only when the user opens the detail that needs them.
- Verify the first action after reload, switching targets, and repeating the
  same action after async data hydrates.

## Visual QA

- Test the changed flow at desktop and narrow viewport sizes.
- Confirm controls align with neighboring controls, labels fit, focus states
  work, and icons reflect real state.
- Use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for tests that create,
  edit, or delete profiles, sessions, workspaces, or settings.
- Include before/after screenshots, video, or equivalent browser evidence for UI
  pull requests.

## Durable Lessons

- Persist only lessons likely to recur across future work.
- Phrase lessons as product or engineering invariants, not personal notes or
  session logs.
- Keep this file short. Move larger design decisions into specs or RFCs when
  they outgrow this checklist.
