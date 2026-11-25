#!/usr/bin/env python3
"""Drive Pliny's local dashboard against disposable state, retaining evidence."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def serve(root, ready):
    # Import only from the disposable installation. No production globals change.
    sys.path.insert(0, str(root))
    import server
    server.ThreadedHTTPServer.address_family = socket.AF_INET
    httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.Handler)
    write_json(ready, {"url": f"http://127.0.0.1:{httpd.server_port}", "pid": os.getpid()})
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def doctor(process, root, ready):
    assert process.poll() is None, "Owned HTTP process exited"
    assert ready["pid"] == process.pid, "Readiness PID does not match our child"
    version = get_json(ready["url"] + "/api/version")
    assert Path(version["home"]).resolve() == root.resolve(), version
    assert Path(version["workshop"]).resolve() == (root / "state/workshop").resolve(), version
    assert version["version"] == (root / "VERSION").read_text(encoding="utf-8").strip()
    status = get_json(ready["url"] + "/api/status")
    assert status["workshop_exists"] and status["active_sessions"] == 0, status
    return {"pid": process.pid, "url": ready["url"], "version": version, "status": status}


def drive(page, context, url, feature, evidence):
    from playwright.sync_api import expect, TimeoutError as PlaywrightTimeout
    actions = []

    def action(description, callback):
        actions.append(description)
        write_json(evidence / "actions.json", actions)
        callback()

    def capture(name):
        page.screenshot(path=str(evidence / f"{name}.png"), full_page=True)
        (evidence / f"{name}.aria.txt").write_text(page.locator("body").aria_snapshot(), encoding="utf-8")

    action("Open Command at /", lambda: page.goto(url, wait_until="domcontentloaded"))
    page.locator("#pliny-pair-code").wait_for(state="visible")
    console = (evidence / "server.log").read_text(encoding="utf-8")
    codes = re.findall(r"pairing code: (\S+)", console)
    assert codes, "Disposable server did not print a pairing code"
    action("Enter this run's console pairing code", lambda: page.locator("#pliny-pair-code").fill(codes[-1]))
    action("Pair this disposable browser", lambda: page.locator("#pliny-pair-go").click())
    expect(page.locator("#pliny-pair")).to_have_count(0)
    # Dismiss the actual first-run tour through its visible control.
    skip = page.locator("#intro-skip")
    try:
        skip.wait_for(state="visible", timeout=3000)
        action("Dismiss introductory tour with Skip", lambda: skip.click())
        expect(page.locator("#intro-overlay")).not_to_be_visible()
    except PlaywrightTimeout:
        if page.locator("#intro-overlay").is_visible():
            raise
    expect(page.locator("#ui-guide-btn")).to_be_visible()
    capture("before")
    if feature == "guide":
        action("Open Guide button", lambda: page.locator("#ui-guide-btn").click())
        drawer = page.get_by_role("dialog", name="Guide", exact=True)
        expect(page.locator("body")).to_have_class(re.compile(r".*ui-guide-open.*"))
        action("Search guide for Mission Queue", lambda: drawer.get_by_role("searchbox", name="Search guide").fill("Mission Queue"))
        expect(drawer.locator(".ui-guide-item")).to_have_count(1)
        expect(drawer.locator('[data-id="queue"]')).to_contain_text("Mission Queue")
        capture("guide-search")
        action("Search guide for a missing term", lambda: drawer.get_by_role("searchbox", name="Search guide").fill("zzverifyplinynomatchzz"))
        expect(drawer.locator(".ui-guide-none")).to_contain_text("No matches")
        capture("guide-empty")
        action("Clear guide search", lambda: drawer.get_by_role("searchbox", name="Search guide").fill(""))
        expect(drawer.locator(".ui-guide-item")).not_to_have_count(0)
        action("Close with Escape", lambda: page.keyboard.press("Escape"))
        expect(page.locator("body")).not_to_have_class(re.compile(r".*ui-guide-open.*"))
        action("Reopen with F1", lambda: page.keyboard.press("F1"))
        expect(page.locator("body")).to_have_class(re.compile(r".*ui-guide-open.*"))
        capture("guide-keyboard")
        action("Close with Close button", lambda: drawer.get_by_role("button", name="Close", exact=True).click())
        action("Reveal Mission Queue from topbar Queue", lambda: page.locator("#pulse-missions").click())
        action("Open queue contextual help", lambda: page.locator('[data-guide="queue"]').click())
        expect(drawer.locator('[data-id="queue"]')).to_have_class(re.compile(r".*flash.*"))
        capture("guide-context")
    elif feature == "queue":
        action("Reveal Mission Queue from topbar Queue", lambda: page.locator("#pulse-missions").click())
        action("Open Add Mission", lambda: page.get_by_role("button", name="+ Add Mission", exact=True).click())
        action("Enter title", lambda: page.locator("#mission-title").fill("Verification queue item"))
        action("Enter prompt", lambda: page.locator("#mission-prompt").fill("Keep this queued. Do not launch an agent."))
        capture("queue-draft")
        action("Queue Mission", lambda: page.locator("#mission-save-btn").click())
        expect(page.locator("#mission-modal")).not_to_be_visible()
        expect(page.locator("#mission-list")).to_contain_text("Verification queue item")
        records = context.request.get(url + "/api/missions").json()
        assert any(m["title"] == "Verification queue item" and m["status"] == "queued" for m in records)
        write_json(evidence / "missions.json", records)
        action("Reload queued state", lambda: page.reload(wait_until="domcontentloaded"))
        expect(page.locator("#mission-list")).to_contain_text("Verification queue item")
        capture("queue-persisted")
    else:
        action("Reveal Dragon Memory from topbar Memory", lambda: page.locator("#pulse-memory").click())
        action("Open New Memory", lambda: page.get_by_role("button", name="+ New Memory", exact=True).click())
        action("Enter memory title", lambda: page.locator("#mem-title").fill("Verification memory"))
        action("Enter memory content", lambda: page.locator("#mem-content").fill("A disposable verification note."))
        capture("memory-draft")
        action("Save Memory", lambda: page.locator("#memory-modal").get_by_role("button", name="Save Memory", exact=True).click())
        expect(page.locator("#memory-list")).to_contain_text("Verification memory")
        action("Reload saved note", lambda: page.reload(wait_until="domcontentloaded"))
        action("Open saved memory", lambda: page.locator("#memory-list .mem-item").filter(has_text="Verification memory").click())
        expect(page.locator(".file-content")).to_contain_text("A disposable verification note.")
        write_json(evidence / "memory.json", context.request.get(url + "/api/memory").json())
        capture("memory-persisted")


def run(feature):
    from playwright.sync_api import sync_playwright
    repo = Path(__file__).resolve().parents[4]
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
    evidence = repo / "tmp/verification/pliny" / stamp
    evidence.mkdir(parents=True)
    scratch = Path(tempfile.mkdtemp(prefix="pliny-verify-")).resolve()
    root = scratch / "app"
    root.mkdir()
    process = None
    passed = False
    try:
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode("utf-8").split("\0")
        manifest = {}
        for name in tracked:
            path = Path(name)
            if len(path.parts) != 1 or (path.suffix not in {".py", ".html", ".js", ".css"} and name != "VERSION"):
                continue
            source = repo / path
            if source.is_file():
                shutil.copy2(source, root / path)
                manifest[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        write_json(evidence / "source.json", manifest)
        # No .env, live state, operator browser profile or inherited provider keys.
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}}
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            env[name] = str(scratch / "profile")
        (scratch / "profile").mkdir()
        env.update(PYTHONUTF8="1", PLINY_HOME=str(root), PLINY_WORKSHOP=str(root / "state/workshop"), PLINY_OPERATOR_TOKEN_FILE="off")
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(os.environ.get("SYSTEMROOT", "/")) / "System32")])
        ready_path = scratch / "ready.json"
        with (evidence / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--serve", str(root), str(ready_path)], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            deadline = time.monotonic() + 30
            while not ready_path.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Launch failed; inspect server.log")
                time.sleep(0.1)
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            health = doctor(process, root, ready)
            write_json(evidence / "doctor.json", health)
            print("Doctor passed:", ready["url"], flush=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.set_default_timeout(10000)
                blocked = []
                def local_only(route):
                    if route.request.url.startswith(ready["url"] + "/"):
                        route.continue_()
                    else:
                        blocked.append(route.request.url)
                        route.abort()
                context.route("**/*", local_only)
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.new_page()
                requests = []
                def record_request(request):
                    requests.append({"method": request.method, "url": request.url})
                page.on("request", record_request)
                try:
                    drive(page, context, ready["url"], feature, evidence)
                    assert get_json(ready["url"] + "/api/status")["active_sessions"] == 0
                    write_json(evidence / "state-files.json", sorted(str(p.relative_to(root)) for p in (root / "state").rglob("*") if p.is_file()))
                    passed = True
                except Exception:
                    page.screenshot(path=str(evidence / "failure.png"), full_page=True)
                    raise
                finally:
                    write_json(evidence / "requests.json", requests)
                    write_json(evidence / "blocked-external.json", blocked)
                    context.tracing.stop(path=str(evidence / "trace.zip"))
                    browser.close()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        # Only delete the absolute directory allocated by this invocation.
        assert scratch.parent == Path(tempfile.gettempdir()).resolve() and scratch.name.startswith("pliny-verify-")
        shutil.rmtree(scratch)
        result = {"feature": feature, "passed": passed, "cleanup": not scratch.exists(), "process_exit": process.returncode if process else None, "mode": "production Handler, foreground HTTP only; server.main lifecycle excluded"}
        write_json(evidence / "result.json", result)
        assert (evidence / "source.json").exists() and (evidence / "result.json").exists()
        print(json.dumps({**result, "evidence": str(evidence)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature", choices=("guide", "queue", "memory"), default="guide")
    parser.add_argument("--serve", nargs=2, metavar=("ROOT", "READY"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(Path(args.serve[0]), Path(args.serve[1]))
    else:
        run(args.feature)
