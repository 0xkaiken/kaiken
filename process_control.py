"""Process groups and containment for agent sessions on Windows and POSIX."""

import csv
import ctypes
import json
import os
import re
import signal
import subprocess
import tempfile
import time


def retry_while_file_busy(operation, attempts=40, delay=0.025):
    """Run a file operation, waiting out Windows' sharing violations.

    Python opens files without FILE_SHARE_DELETE, so while anyone has a file open
    os.replace on it fails with PermissionError, and readers can hit the same error
    during a replace. On a busy state file this used to fail most writes.
    """
    for attempt in range(attempts):
        try:
            return operation()
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(delay)


def replace_file(source, destination):
    retry_while_file_busy(lambda: os.replace(source, destination))


def launch_agent_process(command, input_text=None, **options):
    """Keep long Claude prompts below Windows' command-line size limit."""
    if os.name == "nt" and command[0] == "claude" and command[1] == "-p":
        input_text = command[2]
        command = command[:2] + command[3:]
    if input_text is not None:
        with tempfile.TemporaryFile() as prompt_input:
            prompt_input.write(input_text.encode("utf-8"))
            prompt_input.seek(0)
            return subprocess.Popen(command, stdin=prompt_input, **options)
    return subprocess.Popen(command, **options)


def run_captured_process(command, *, input_text, timeout, env=None, cwd=None):
    """Run a UTF-8 text command with bounded waits and owned-child cleanup.

    Descendants can inherit stdout/stderr and keep PIPE readers alive after
    the root exits or times out. Files avoid that unbounded drain; a Windows
    job or POSIX process group contains the descendants on every exit path.

    ``cwd`` is passed through to the child. Target-route callers use it to run
    the model in a workspace that does not document the attack.
    """
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        job = create_kill_on_close_job()
        if os.name == "nt" and not job:
            raise OSError("Cannot create process containment for text request")
        process = None
        assigned = False
        try:
            process = launch_agent_process(command, input_text=input_text,
                                           stdout=stdout, stderr=stderr, env=env,
                                           cwd=cwd, **process_group_options())
            assigned = bool(job and assign_to_job(job, process))
            if os.name == "nt" and not assigned:
                raise OSError("Cannot contain text request process")
            process.wait(timeout=timeout)
        finally:
            if job:
                terminate_job(job)
            if process is not None:
                if os.name != "nt":
                    try:
                        # The root may have exited while its descendants live.
                        signal_process_group(process.pid, force=True)
                    except ProcessLookupError:
                        pass
                elif not assigned:
                    stop_process_tree(process, force=True)
                process.wait(timeout=5)
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, process.returncode,
                                           stdout.read().decode("utf-8", errors="replace"),
                                           stderr.read().decode("utf-8", errors="replace"))


# ─── Windows Job Objects ─────────────────────────────────────────────────────
# taskkill /T only walks the parent/child tree at kill time. When a runner
# process exits on its own (e.g. opencode_runner.py dies with code 1 while
# its `opencode run` child keeps working), the descendant is reparented and
# survives ANY later tree kill — that is exactly how the 2026-11-01 orphan
# agent kept probing GPT-6.1-Sol for ~50 minutes after its session died.
#
# A job object fixes this at the kernel level: every process spawned into the
# job (and all its descendants) is terminated when the job handle closes,
# even if the root process already exited. Best-effort on non-Windows.

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def create_kill_on_close_job():
    """Create a job object that kills all members when the handle closes.

    Returns an opaque handle (int) or None when unavailable. The server
    holds one per session and closes it on stop OR natural exit, so even
    reparented grandchildren cannot outlive the session.
    """
    if os.name != "nt":
        return None
    try:
        k32 = _kernel32()
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JobExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        if not k32.SetInformationJobObject(
                job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def assign_to_job(job, process):
    """Put a freshly spawned process (and thus its descendants) in the job."""
    if not job or os.name != "nt" or process is None:
        return False
    try:
        handle = getattr(process, "_handle", None)
        if not handle:
            return False
        k32 = _kernel32()
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return bool(k32.AssignProcessToJobObject(job, int(handle)))
    except Exception:
        return False


def terminate_job(job):
    """Kill every process in the job and close the handle."""
    if not job or os.name != "nt":
        return
    try:
        k32 = _kernel32()
        k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.TerminateJobObject(job, 1)
        k32.CloseHandle(job)
    except Exception:
        pass


# ─── Orphan agent sweep ──────────────────────────────────────────────────────
# Last line of defence for STOP ALL and for sessions older than the job-object
# fix. Matches ONLY our headless runners, never the server, the sweep helper,
# an interactive OpenCode/Claude session, or the user's own shells.

PLINY_TITLE_PREFIX = "pliny-command:"


def _norm_path(value):
    return str(value or "").replace("\\", "/").lower()


def _is_orphan_agent(name, cmdline, root=None, workshop=None):
    """True only for Libertarium's own headless agents.

    With ``root``/``workshop`` supplied (the STOP ALL path), a process must
    carry an ownership marker: an absolute runner path under this install, a
    command-line reference to this install's workshop, or OpenCode's
    ``--title pliny-command:<session>`` marker. A bare ``claude --print`` or
    ``opencode run`` from another project can no longer be swept away.

    Without those arguments the historical matcher is preserved for callers
    that have not opted into the ownership boundary.
    """
    low = cmdline.lower()
    if root or workshop:
        markers = [marker for marker in (_norm_path(root), _norm_path(workshop)) if marker]

        def owned_reference(text):
            haystack = _norm_path(text)
            for marker in markers:
                index = haystack.find(marker)
                while index >= 0:
                    after = haystack[index + len(marker):index + len(marker) + 1]
                    if not after or after in "/\\\"' ":
                        return True
                    index = haystack.find(marker, index + 1)
            return False

        owned = owned_reference(cmdline)
        if name.startswith("python") and owned and any(
                script in low for script in
                ("opencode_runner.py", "codex_runner.py", "rt_hermes.py")):
            return True
        if name.startswith("opencode") and "--standalone" in low and " run " in low \
                and PLINY_TITLE_PREFIX in low:
            return True
        if name.startswith("claude") and owned and "stream-json" in low \
                and "--output-format" in low:
            return True
        return False
    # Legacy matcher (kept for external callers; STOP ALL always passes root).
    if name.startswith("python") and any(
            script in low for script in
            ("opencode_runner.py", "codex_runner.py", "rt_hermes.py")):
        return True
    if name.startswith("opencode") and "--standalone" in low and " run " in low:
        return True
    if name.startswith("claude") and "--dangerously-skip-permissions" in low \
            and "stream-json" in low:
        return True
    return False


def _process_snapshot():
    """Return [(pid, parent_pid, name, cmdline)] for Windows processes."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command",
             "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
             "Get-CimInstance Win32_Process | "
             "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    rows, seen = [], set()
    for rec in data:
        try:
            pid = int(rec.get("ProcessId") or 0)
            ppid = int(rec.get("ParentProcessId") or 0)
            name = (rec.get("Name") or "").lower()
            cmdline = rec.get("CommandLine") or ""
        except (TypeError, ValueError):
            continue
        if pid:
            rows.append((pid, ppid, name, cmdline))
            seen.add(pid)
    # Windows reuses PIDs; drop rows whose parent PID is clearly unrelated.
    return rows


def _ancestor_pids(rows, pid):
    parents = {r[0]: r[1] for r in rows}
    seen, cur = set(), parents.get(pid, 0)
    while cur and cur not in seen:
        seen.add(cur)
        cur = parents.get(cur, 0)
    return seen


def windows_python_script_pids(script, snapshot=None):
    """Find Python processes running this exact absolute script path."""
    target = os.path.abspath(script).replace("\\", "/").lower()
    # Only accept the script argument, never a path mentioned in `python -c`
    # or passed to a different Python program.
    pattern = re.compile(
        r'^(?:"[^"]+"|\S+)\s+'
        r'(?:(?:-[uBEIsSObqv]+|-X\s*\S+|-W\s*\S+)\s+)*'
        r'(?:"' + re.escape(target) + r'"|' + re.escape(target) + r')(?=\s|$)', re.IGNORECASE)
    rows = _process_snapshot() if snapshot is None else snapshot
    return {pid for pid, _parent, name, command in rows
            if name.startswith(("python", "pypy"))
            and pattern.search(command.replace("\\", "/").lower())}


def windows_project_server_pids(root, snapshot=None):
    """Find this project's servers, including older relative-path launchers."""
    rows = _process_snapshot() if snapshot is None else snapshot
    protected = _ancestor_pids(rows, os.getpid()) | {os.getpid()}
    commands = {pid: command for pid, _parent, _name, command in rows}
    run_flag = re.compile(r'(?:^|\s)--run(?=\s|$)')
    targets = windows_python_script_pids(os.path.join(root, "server.py"), rows)
    targets.update(pid for pid in windows_python_script_pids(os.path.join(root, "install_windows.py"), rows)
                   if run_flag.search(commands[pid]))

    # Old start-windows.cmd versions used a relative script argument. The
    # indicator's absolute path and --parent identify their project safely.
    indicators = windows_python_script_pids(os.path.join(root, "computer_use_indicator.py"), rows)
    legacy_script = re.compile(
        r'^(?:"[^"]+"|\S+)\s+'
        r'(?:(?:-[uBEIsSObqv]+|-X\s*\S+|-W\s*\S+)\s+)*'
        r'"?(server\.py|install_windows\.py)"?(?=\s|$)', re.IGNORECASE)
    parents = {pid: (name, command) for pid, _parent, name, command in rows}
    for pid, parent, _name, command in rows:
        if pid not in indicators or not re.search(r'(?:^|\s)--parent\s+' + str(parent) + r'(?=\s|$)', command):
            continue
        name, parent_command = parents.get(parent, ("", ""))
        match = legacy_script.search(parent_command)
        if name.startswith(("python", "pypy")) and match:
            if match.group(1).lower() == "server.py" or run_flag.search(parent_command):
                targets.add(parent)
    return targets - protected


STILL_ACTIVE = 259


def process_is_alive(pid):
    """True when ``pid`` still exists. Cheap liveness check before a kill."""
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False
    try:
        k32 = _kernel32()
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.CloseHandle(handle)
    except Exception:
        # Unknown: let taskkill make the final call.
        return True


def sweep_orphan_agents(root=None, workshop=None, protect_live_servers=True):
    """Kill leftover agent processes that no longer belong to a session.

    The ownership boundary defaults to this module's install directory, so a
    no-argument call is still safe. When ``protect_live_servers`` is set (the
    default), a candidate whose ancestor chain includes another *live* server
    for this install is left alone: that agent belongs to a running instance,
    not to a previous crash. The current process and its ancestors are always
    protected. Returns the number of root PIDs terminated. Windows only;
    POSIX callers use the scoped pkill patterns in server.stop_all_sessions.
    """
    if os.name != "nt":
        return 0
    root = root or os.path.dirname(os.path.abspath(__file__))
    workshop = workshop or os.path.join(root, "state", "workshop")
    rows = _process_snapshot()
    if not rows:
        return 0
    protected = _ancestor_pids(rows, os.getpid()) | {os.getpid()}
    live_servers = set()
    if protect_live_servers:
        # Excludes this process and its ancestors by construction, so a
        # shutdown sweep still collects its own agents after stopping them.
        live_servers = windows_project_server_pids(root, snapshot=rows) - protected
    killed = 0
    for pid, _ppid, name, cmdline in rows:
        if pid in protected or not _is_orphan_agent(
                name, cmdline, root=root, workshop=workshop):
            continue
        if live_servers and (_ancestor_pids(rows, pid) & live_servers):
            # Owned by a server that is still running: not an orphan.
            continue
        if not process_is_alive(pid):
            # Snapshot is a few hundred milliseconds old; the process may
            # already be gone, and a reused PID must not be killed.
            continue
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
            )
            if result.returncode == 0:
                killed += 1
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


def process_group_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def process_group_id(pid):
    return pid if os.name == "nt" else os.getpgid(pid)


def process_group_exists(group_id):
    if os.name != "nt":
        os.killpg(group_id, 0)
        return True
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {group_id}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="replace", check=True,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=5,
    )
    return any(len(row) > 1 and row[1] == str(group_id)
               for row in csv.reader(result.stdout.splitlines()))


def signal_process_group(group_id, force=False):
    if group_id <= 1 or group_id == process_group_id(os.getpid()):
        raise ValueError("refusing to terminate the server's process group")
    if os.name == "nt":
        # Headless CLI processes have no console for CTRL_BREAK_EVENT.
        # /T is essential: terminating only the CLI leaves its tools alive.
        result = subprocess.run(
            ["taskkill", "/PID", str(group_id), "/T", "/F"],
            capture_output=True, text=True, errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
        )
        if result.returncode and process_group_exists(group_id):
            raise OSError(result.stderr.strip() or "taskkill failed")
    else:
        os.killpg(group_id, signal.SIGKILL if force else signal.SIGTERM)


def stop_process_tree(process, force=False):
    if process.poll() is not None:
        return
    try:
        group_id = process_group_id(process.pid)
        signal_process_group(group_id, force=force)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            signal_process_group(group_id, force=True)
            process.wait(timeout=5)
    except ProcessLookupError:
        pass
