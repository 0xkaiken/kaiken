#!/usr/bin/env python3
"""gh_l1b3rt4s_pr — turn a gauntlet run into an L1B3RT4S entry and open a PR.

Called by the gauntlet report footer as::

    python3 tools/gh_l1b3rt4s_pr.py --run-id 20261031-180401-dc3680 --dry-run
    python3 tools/gh_l1b3rt4s_pr.py --run-id 20261031-180401-dc3680

Dry run writes the ``.mkd`` entry into ``<workshop>/l1b3rt4s_staging/`` and
prints it.  Live mode additionally needs ``GITHUB_PAT`` (or ``PLINY_GITHUB_PAT``)
and a target repo, and then:

  1. reads the base branch head,
  2. creates ``l1b3rt4s/<run-id>``,
  3. writes one file per cracked target,
  4. opens the pull request.

Nothing is pushed without the token; the tool exits 2 with the reason instead
of half-doing it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def workshop_dir() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def home() -> Path:
    try:
        from _paths import pliny_home
        return pliny_home()
    except Exception:
        env = os.environ.get("PLINY_HOME")
        if env:
            return Path(env).expanduser().resolve()
        return ROOT


def resolve_library() -> Path:
    return home() / "rt_library"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "target"


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _staging_entries(run_id: str) -> list[dict]:
    candidates = []
    try:
        from _paths import state_dir
        candidates.append(state_dir() / "l1b3rt4s_staging")
    except Exception:
        pass
    candidates.append(workshop_dir() / "l1b3rt4s_staging")

    out = []
    seen: set[str] = set()
    for d in candidates:
        if not d.is_dir():
            continue
        for f in sorted(d.glob(f"{run_id}_*.json")):
            if f.name in seen:
                continue
            seen.add(f.name)
            data = _load_json(f)
            if data:
                data["_source"] = str(f)
                out.append(data)
    return out


def _entry_from_staging(data: dict) -> str:
    if data.get("mkd"):
        return data["mkd"]
    return "\n".join([
        f"# {data.get('target_name', 'UNKNOWN')} — {data.get('behavior', '')}",
        "",
        f"> Cracked by @elder_plinius | {str(data.get('timestamp', ''))[:10]}",
        f"> Technique: {data.get('technique', 'n/a')}",
        f"> Agent: {data.get('agent', 'n/a')}",
        f"> Score: {data.get('score', '?')}/3",
        f"> Gauntlet run: `{data.get('run_id', '')}`",
        "",
        "```",
        (data.get("prompt") or "").strip(),
        "```",
        "",
    ])


def _entry_from_state(run: dict, target: dict) -> str:
    markers = target.get("markers_hit") or []
    if isinstance(markers, dict):
        markers = [markers]
    lines = [
        f"# {target.get('name', target.get('id', 'UNKNOWN'))} — "
        f"{target.get('assigned_behavior_name', 'cracked')}",
        "",
        f"> Cracked by @elder_plinius | {str(run.get('ended_at', ''))[:10]}",
        f"> Model: `{target.get('model') or (str(target.get('provider', '')) + '/' + str(target.get('model', '')))}`",
        f"> Agent: {target.get('agent_history', 'n/a')}",
        f"> Gauntlet run: `{run.get('id', '')}`",
        "",
    ]
    for m in markers:
        if not isinstance(m, dict):
            continue
        lines.append(f"**{m.get('name', m.get('id', 'marker'))}** — judge "
                     f"{m.get('judge_score', '?')}/3")
        if m.get("judge_reason"):
            lines.append("")
            lines.append(f"> {m['judge_reason'].strip()}")
        lines.append("")
    lines.append("Winning prompt is in the run transcript: "
                 f"`transcripts/{target.get('id', '')}.jsonl`")
    lines.append("")
    return "\n".join(lines)


def collect(run_id: str) -> tuple[dict, list[tuple[str, str]]]:
    run = _load_json(resolve_library() / "gauntlets" / "runs" / run_id / "state.json")
    if not run:
        raise SystemExit(f"error: run not found: {run_id} "
                         f"(looked in {resolve_library() / 'gauntlets' / 'runs'})")

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    for data in _staging_entries(run_id):
        name = _slug(data.get("target_name") or data.get("target_id") or "target")
        entries.append((name, _entry_from_staging(data)))
        seen.add(name)

    for target in run.get("targets", []):
        if target.get("status") != "cracked":
            continue
        name = _slug(target.get("name") or target.get("id") or "target")
        if name in seen:
            continue
        entries.append((name, _entry_from_state(run, target)))

    return run, entries


def write_staging(run_id: str, entries: list[tuple[str, str]]) -> list[Path]:
    d = workshop_dir() / "l1b3rt4s_staging"
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in entries:
        path = d / f"pr_{run_id}_{name}.mkd"
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written


# ---- GitHub --------------------------------------------------------------

def _api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pliny-command/gh_l1b3rt4s_pr",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"message": body[:400]}
        return exc.code, parsed


def open_pr(repo: str, base: str, run_id: str, entries: list[tuple[str, str]],
            token: str, dir_hint: str) -> int:
    branch = f"l1b3rt4s/{run_id}"
    api = f"https://api.github.com/repos/{repo}"

    code, ref = _api("GET", f"{api}/git/ref/heads/{base}", token)
    if code != 200:
        print(f"error: cannot read {repo}@{base}: {code} {ref.get('message')}",
              file=sys.stderr)
        return 2
    base_sha = ref["object"]["sha"]

    code, _ = _api("POST", f"{api}/git/refs", token,
                   {"ref": f"refs/heads/{branch}", "sha": base_sha})
    if code not in (201, 422):  # 422 = branch already exists
        print(f"error: cannot create branch {branch}: {code}", file=sys.stderr)
        return 2

    for name, body in entries:
        path = f"{dir_hint.rstrip('/')}/{name.upper()}.mkd"
        code, existing = _api("GET", f"{api}/contents/{path}?ref={branch}", token)
        payload = {
            "message": f"l1b3rt4s: {name} ({run_id})",
            "content": __import__("base64").b64encode(body.encode()).decode(),
            "branch": branch,
        }
        if code == 200 and existing.get("sha"):
            payload["sha"] = existing["sha"]
        code, resp = _api("PUT", f"{api}/contents/{path}", token, payload)
        if code not in (200, 201):
            print(f"error: write failed for {path}: {code} {resp.get('message')}",
                  file=sys.stderr)
            return 2
        print(f"wrote {path}")

    code, pr = _api("POST", f"{api}/pulls", token, {
        "title": f"l1b3rt4s: run {run_id}",
        "head": branch,
        "base": base,
        "body": (f"Automated entry from gauntlet run `{run_id}`.\n\n"
                 f"Cracked targets: {len(entries)}\n"),
    })
    if code not in (200, 201):
        print(f"error: cannot open PR: {code} {pr.get('message')}", file=sys.stderr)
        return 2
    print(f"PR opened: {pr.get('html_url')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gh_l1b3rt4s_pr", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--repo", default=os.environ.get("PLINY_L1B3RT4S_REPO", ""),
                   help="owner/name (or PLINY_L1B3RT4S_REPO)")
    p.add_argument("--base", default=os.environ.get("PLINY_L1B3RT4S_BASE", "main"))
    p.add_argument("--dir", default="L1B3RT4S",
                   help="target directory inside the repo")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    run, entries = collect(args.run_id)
    if not entries:
        print(f"no cracked targets in run {args.run_id} — nothing to submit")
        return 0

    written = write_staging(args.run_id, entries)

    if args.json:
        print(json.dumps({"run_id": args.run_id,
                          "cracked": [n for n, _ in entries],
                          "staged": [str(w) for w in written]}, indent=2))
    else:
        print(f"run {args.run_id}: {len(entries)} cracked target(s)")
        for path in written:
            print(f"  staged: {path}")

    if args.dry_run:
        print("\n----- preview -----\n")
        print(entries[0][1])
        return 0

    token = os.environ.get("GITHUB_PAT") or os.environ.get("PLINY_GITHUB_PAT", "")
    if not token:
        print("\nlive submit needs GITHUB_PAT (or PLINY_GITHUB_PAT). "
              "Staged files are ready; rerun with the token set.",
              file=sys.stderr)
        return 2
    if not args.repo or "/" not in args.repo:
        print("\nlive submit needs --repo owner/name (or PLINY_L1B3RT4S_REPO).",
              file=sys.stderr)
        return 2

    return open_pr(args.repo, args.base, args.run_id, entries, token, args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
