# Mission queue

Users save work for later and see it again after reloading Command. Saving
an item does not need to start an agent.

## Sub-features

- `queue-create` saves a title and prompt through Add Mission.
- `queue-persist` shows the queued item again after reload.
- Editing, deletion, Cancel and Next are additional controls not covered by
  this initial create-and-reload recipe.

## How to get to it (user POV)

- Open `/` and find Mission Queue, then choose `+ Add Mission`.
- The topbar Queue pulse `#pulse-missions` reveals the section. The helper
  uses this entry before choosing Add Mission.

## Driving it with Playwright

Preconditions: doctor passes and there are no missions in disposable state.
This source-derived recipe is not proven until its own command succeeds.

- Run `python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature queue`.
- **Create.** Click `#pulse-missions`, then choose `+ Add Mission`. Fill `#mission-title` with
  `Verification queue item`, and `#mission-prompt` with
  `Keep this queued. Do not launch an agent.`. Capture `queue-draft.png`.
- **Save.** Click `#mission-save-btn`, labeled `Queue Mission`. Require
  `#mission-modal` to close and `#mission-list` to contain the title.
- **Second view.** GET `/api/missions` must return the same title with
  `status: queued`. Save the response as `missions.json`.
- **Persistence.** Reload `/` and require the title in `#mission-list` again.
  Capture `queue-persisted.png` and its ARIA snapshot. The final status must
  still report zero active sessions.

## Gotchas

- Next starts a runtime and is outside this journey. Do not click it.
- An empty prompt cannot save. A toast alone does not prove persistence.
- No seed data is required. Missing Add Mission or failure to save is a
  failed journey; do not insert records directly to make it pass.
- This recipe does not establish edit, delete, cancel or priority ordering.
