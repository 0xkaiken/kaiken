# Guide

Guide explains dashboard controls, marks actions that start agents or affect
the real world, and lets users search those explanations.

## Sub-features

- `guide-button` opens and closes the drawer from its button.
- `guide-keyboard` opens with F1 and closes with Escape or F1.
- `guide-context` opens the matching explanation from a `?` control.
- `guide-search` supports matching, empty and cleared searches.

## How to get to it (user POV)

- On Command `/`, choose Guide or press F1.
- With no active sessions, the center panel also offers
  `Not sure what something does? Open the guide`. This entry is not driven
  by the default recipe.
- In Mission Queue, choose the `?` beside its heading.
- Guide and F1 also exist on `/redteam` and `/arcade`; those page variants
  are separate entry points and remain unverified by the default recipe.

## Driving it with Playwright

Preconditions: the isolated instance passes doctor, the Command page loads
and the introductory tour is dismissed.

- Run `python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature guide`.
- **Button.** Click `#ui-guide-btn`. Require `body.ui-guide-open` and the
  dialog named `Guide`. Capture the initial page before opening.
- **Match.** Fill searchbox `Search guide` with `Mission Queue`. Require
  exactly one `.ui-guide-item`, with `[data-id="queue"]` and that title.
- **Miss.** Fill `zzverifyplinynomatchzz`. Require `.ui-guide-none` containing
  `No matches`. Capture both matching and empty results.
- **Clear.** Empty the searchbox. Require guide items to return.
- **Keyboard.** Press Escape, require the open class to disappear. Press F1,
  require it to return and capture `guide-keyboard.png`.
- **Context.** Close via the button named `Close`, reveal Mission Queue via
  `#pulse-missions` in the topbar, then click
  `[data-guide="queue"]`. Require the matching article's `flash` class and
  capture `guide-context.png` plus its ARIA snapshot.

## Gotchas

- The drawer remains in the DOM when closed. Assert `body.ui-guide-open`,
  not the presence of the dialog element alone.
- The first-run tour's dismiss button is `Skip`.
- Mission Queue may be hidden until the topbar Queue pulse reveals it.
- The default proof covers the queue `?`, not every contextual control.
- F1 closes an already open drawer. The default journey tests opening via
  F1 and closing via Escape/Close; F1 toggle-close remains a separate check.
- The default proof does not test Guide's `Open →` navigation or replay tour.
