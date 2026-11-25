# Dragon Memory

Users save a note that later sessions can read and reopen its full content
from the dashboard's Files view.

## Sub-features

- `memory-create` saves a titled note through New Memory.
- `memory-reopen` reloads and opens the saved content.
- Memory type changes, Cancel and Delete are not covered by this recipe.

## How to get to it (user POV)

- Open `/`, find Dragon Memory and choose `+ New Memory`.
- Select a saved row to read it in Files.
- The topbar Memory pulse `#pulse-memory` reveals the section. The helper
  uses this entry before choosing New Memory.

## Driving it with Playwright

Preconditions: doctor passes and disposable memory state is empty. This
source-derived recipe is not proven until its own command succeeds.

- Run `python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature memory`.
- **Draft.** Click `#pulse-memory`, then choose `+ New Memory`. Fill `#mem-title` with
  `Verification memory`, and `#mem-content` with
  `A disposable verification note.`. Capture `memory-draft.png`.
- **Save.** In `#memory-modal`, choose `Save Memory`. Require the title in
  `#memory-list`.
- **Reopen.** Reload, then click the `.mem-item` with that title. Require
  `.file-content` to contain the saved full content.
- **Proof.** Save GET `/api/memory` as `memory.json`, then capture
  `memory-persisted.png` and its ARIA snapshot. Preserve `state-files.json`
  alongside the before and draft views.

## Gotchas

- Title and content are both required. Do not populate the index directly
  if the form fails.
- Reading a note switches to Files. Assert the full content there, rather
  than relying on the truncated list preview.
- No account or provider key is required. No agent needs to be started.
- The proof does not establish that a future launched agent consumes the
  note; that requires a separately authorized runtime journey.
