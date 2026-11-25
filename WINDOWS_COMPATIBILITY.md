# Windows compatibility report

Reviewed on November 1, 2026, on native Windows with Python 3.12.8. This
checkout already contained Windows desktop control, process management and
CLI-runner changes. Those were preserved. This pass focused on setup,
background services, browser control and screenshot/file handling.

## Issues fixed in this pass

| Issue | Effect on Windows | Change |
| --- | --- | --- |
| Watchdog used `os.kill(pid, 0)` as a liveness probe | Could terminate the process whose lock was being checked, even in observation mode | Uses the existing Windows process query; an inconclusive query preserves the lock |
| Installer and primary setup instructions required Bash and Make | A fresh native Windows checkout had no equivalent dependency/configuration setup | Added `install_windows.py`; `start-windows.cmd` selects `python` or `py -3`, installs missing PyYAML, seeds missing files, and starts the server with UTF-8 |
| Browser recovery required `pgrep` and `pkill` | The health monitor could not detect/restart the browser on Windows | Uses UTF-8 Windows process snapshots and process-tree termination, matching the exact script path in this checkout, including paths with spaces and international characters |
| Some browser launches left stderr in an unread pipe | Repeated browser errors could fill the pipe and stall the browser | All server-managed Playwright launches write to `logs/browser.log` and use UTF-8 and process groups |
| Firefox cookie fallback depended on an external `sqlite3` executable | Without that executable, a raw database copy could miss login cookies committed to the WAL | Python's SQLite backup API snapshots the live database, including its WAL, through a read-only source connection |
| Screenshot/proof files used Unix `/tmp` paths | Files could be written to a nonexistent drive-root directory or missed by readers | Runtime artifact paths use the Windows temp directory; screenshot readers retain legacy `/tmp` fallbacks |
| Browser submission and reply instructions used Mac shortcuts and `pbcopy` | Command-key shortcuts and clipboard preparation failed on Windows | Browser controls use Control shortcuts; Windows reply missions receive instructions to enter the supplied text directly |
| Screenshot renderers searched only Mac font directories | Proof images fell back to a small default font | Added the Windows Consolas font path to all three renderer font searches |
| Shared JSON state helpers used the Windows locale encoding | UTF-8 state containing international text could be misread outside the launcher | Shared JSON reads and writes explicitly use UTF-8 |
| Workshop sweeper hardcoded `/opt/homebrew/bin/python3` | The endpoint could not invoke its script on Windows | Uses the server's Python interpreter; the optional workshop script must still be installed |
| Ctrl+C handler called `shutdown()` from the HTTP serving thread | Stopping from the launch console could deadlock | Closes the listener and exits after stopping sessions, without waiting on the same thread |
| Legacy Agent Browser API advertised a Mac-only manager on Windows | Provisioning failed with misleading missing-profile errors | Status explains the supported Browser mode; legacy provision/close requests return an explicit unsupported response |

## Issues fixed in the November 2 pass

| Issue | Effect on Windows | Change |
| --- | --- | --- |
| `os.replace` while another thread had the JSON file open | Measured 93% of writes failing with `PermissionError`, and reads failing during a replace | `replace_file()` and `retry_while_file_busy()` in `process_control.py`; `load_json` shares the writers' lock |
| Relative paths built with `str()` | Backslashes reached pages and agents that split on `/`, so Lair rooms and file activity were wrong | `as_posix()` on every `rel_path` |
| STOP during session preparation | No process existed yet, so the session was marked killed and the agent started anyway, untracked | `_stop_requested` checked under `_spawn_lock` before every spawn |
| Starting `server.py` without UTF-8 mode | State files were read with the ANSI codepage | `server.py` restarts itself with `-X utf8` |
| Hermes looked up only in `~/.local/bin` | Sign-in and sign-out reported the CLI as missing | `hermes_cli()` searches PATH; login opens its own console |
| OpenCode status depended on its background service | A stale service holding the port showed "not connected" and stalled the status call | `--standalone` for status and login |
| Installer skipped Pillow, Playwright and its Firefox | Browser mode and proof images failed on a fresh machine | `install_optional_features()`; failures only warn |
| Prompts used Safari, `python3`, `/tmp/`, `Cmd+` | Agents received commands for another OS | Firefox in the prompts; `windows_native_prompt()` for the rest |

Python documents that Windows `os.kill` calls with values other than console
control events terminate the process. This includes the former zero-signal
probe. See the [Python documentation](https://docs.python.org/3/library/os.html#os.kill).

## Remaining limits

| Component | Status / Windows alternative |
| --- | --- |
| `install.sh`, `quickstart.sh`, `Makefile` | Remain POSIX tooling. Use `start-windows.cmd` on native Windows. Stop with Ctrl+C and rerun to restart. |
| `agent_browser.py` | Legacy standalone manager uses AppleScript, `screencapture`, `cliclick`, POSIX process groups and Mac Firefox paths. Use the dashboard's Playwright Browser mode instead. |
| `computer_use.py`, `tweet.py` | Standalone Mac desktop automation still requires Mac tools. Use the dashboard's existing Windows Desktop or Browser adapter. These standalone programs were not ported. |
| Keychain sidecar and Hermes sign-in helpers | Refer to macOS services or custom `~/.local/bin/hermes` installations. The sidecar/setup sources are not in this checkout. Their behavior cannot be validated or ported from the available files. |
| Agent-generated shell instructions | `windows_native_prompt()` in `server.py` translates `python3`, `/tmp/`, `Cmd+` and `Meta+` in every prompt on its way to an agent, except Custom missions and gauntlet prompts, whose payload files use literal `/tmp` paths. Other Bash syntax in shared templates (`jq`, heredocs, `2>/dev/null`) is not translated. |
| Browser fingerprints | `pw_browser.py` still supplies a Mac Firefox user-agent/version fallback. Browser control works, but Windows authentication/site behavior needs live validation. |
| Optional dependencies | Browser mode requires Playwright and its Firefox download. Desktop control and screenshot rendering require Pillow. The native installer installs only the existing core dependency, PyYAML. |
| Windows desktop restrictions | The existing adapter shares the operator's interactive desktop. Locked screens, UAC and elevated applications still need operator intervention. |

## Validation

Targeted checks cover native process liveness without termination, safe browser
process selection, recovery without Unix commands, cookie WAL snapshots,
Unicode/spaces in setup and launcher paths, non-destructive configuration
seeding, browser keyboard mappings, reply prompt generation, UTF-8 state,
screenshot rendering and HTTP 200 responses from all three dashboards.
All 23 targeted tests passed. `git diff --check` also passed.

```powershell
python -X utf8 -m unittest -v test_windows_compat test_process_control test_browser_windows
git diff --check
```

The installed Claude, Codex and OpenCode executables also answered `--version`
successfully through this checkout's Windows resolution paths. These checks
did not authenticate, call paid models, post content, operate the desktop or
start scheduled/background agent missions. Live provider sessions, Firefox
site logins, GUI input and a real console Ctrl+C shutdown remain untested in
this pass. The full test suite was not required for these targeted changes.
