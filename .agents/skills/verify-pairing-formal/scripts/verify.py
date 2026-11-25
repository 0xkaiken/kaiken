#!/usr/bin/env python3
"""Check the single-use pairing invariant and its connection to server.py."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.request

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[2]
JAR_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
JAR_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"


def dump(value):
    return ast.dump(value, include_attributes=False)


def digest(node):
    return hashlib.sha256(dump(node).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def functions(source):
    return {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}


def without_lock(node):
    node = deepcopy(node)
    locks = [n for n in ast.walk(node) if isinstance(n, ast.With) and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == "_pair_lock" for i in n.items)]
    assert len(locks) == 1, "Expected exactly one redemption critical section"
    lock = locks[0]
    class RemoveLock(ast.NodeTransformer):
        def visit_With(self, current):
            return ast.copy_location(ast.If(test=ast.Constant(True), body=current.body, orelse=[]), current) if current is lock else current
    return ast.fix_missing_locations(RemoveLock().visit(node))


def check_source(source):
    coverage = json.loads((SKILL / "coverage.json").read_text())
    nodes = functions(source)
    mutant = False
    for name, expected in coverage["functions"].items():
        node = nodes.get(name)
        if node is None:
            raise ValueError(f"Covered function moved or disappeared: {name}; review the model")
        if digest(node) == expected["ast_sha256"]:
            continue
        if name == "_pair_code_matches":
            # Only the exact, deliberately supported broken variant is modeled.
            candidate = deepcopy(node)
            replaced = 0
            for parent in ast.walk(candidate):
                for field, values in ast.iter_fields(parent):
                    if not isinstance(values, list):
                        continue
                    for index, value in enumerate(values):
                        if isinstance(value, ast.If) and isinstance(value.test, ast.Constant) and value.test.value is True and not value.orelse:
                            values[index] = ast.With(items=[ast.withitem(context_expr=ast.Name(id="_pair_lock", ctx=ast.Load()))], body=value.body)
                            replaced += 1
            if replaced == 1 and digest(candidate) == expected["ast_sha256"]:
                mutant = True
                continue
        raise ValueError(f"Verification drift: {name} changed; review and update coverage and model before claiming a pass")
    tree = ast.parse(source)
    lock_assignments = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_pair_lock" for t in n.targets)]
    if len(lock_assignments) != 1 or dump(lock_assignments[0].value) != dump(ast.parse("threading.Lock()", mode="eval").body):
        raise ValueError("_pair_lock is no longer one threading.Lock; review atomicity")
    # A newly introduced writer must not silently escape the coverage record.
    writers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id == "_pair_code":
                    writers.add(node.name)
    if writers != set(coverage["pair_code_references"]):
        raise ValueError(f"Pair-code users changed: {sorted(writers)}; review assumptions")
    return nodes, not mutant


def load_real_functions(nodes, lock):
    # Compile the actual reviewed function ASTs. Importing server would load
    # .env and create runtime state, so the rest of the module is not executed.
    module = ast.Module(body=[deepcopy(nodes[name]) for name in ("_code_is_live", "_pair_code_matches")], type_ignores=[])
    namespace = {"PAIR_CODE_TTL_SECONDS": 60, "_pair_lock": lock, "_pair_code": {"value": "fixture-code", "issued_at": 100.0}, "time": SimpleNamespace(time=lambda: 101.0)}
    exec(compile(ast.fix_missing_locations(module), "server.py:pairing-extract", "exec"), namespace)
    return namespace


def replay(nodes, atomic):
    if atomic:
        namespace = load_real_functions(nodes, threading.Lock())
        start = threading.Barrier(2)
        def worker():
            start.wait(timeout=5)
            return namespace["_pair_code_matches"]("fixture-code")
    else:
        # Force both real calls to cache `code` and `issued` before either
        # comparison returns. sys.settrace pauses at the real comparison line;
        # it does not replace hmac.compare_digest or any product operation.
        compare_line = next(n.lineno for n in ast.walk(nodes["_pair_code_matches"]) if isinstance(n, ast.If) and "compare_digest" in ast.unparse(n.test))
        namespace = load_real_functions(nodes, threading.Lock())
        cached = threading.Barrier(2)
        def worker():
            def trace(frame, event, arg):
                if event == "line" and frame.f_code is namespace["_pair_code_matches"].__code__ and frame.f_lineno == compare_line:
                    cached.wait(timeout=5)
                return trace
            sys.settrace(trace)
            try:
                return namespace["_pair_code_matches"]("fixture-code")
            finally:
                sys.settrace(None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        outcomes = [f.result(timeout=10) for f in futures]
    return {"outcomes": outcomes, "accepted": sum(outcomes), "expected_atomic": atomic}


def jar():
    path = REPO / "tmp/verification/tools/tla2tools-1.7.4.jar"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with urllib.request.urlopen(JAR_URL, timeout=45) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != JAR_SHA256:
            raise ValueError("Downloaded TLC checksum mismatch")
        path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError("Cached TLC checksum mismatch")
    return path


def check(source_path, out):
    source = source_path.read_text(encoding="utf-8")
    nodes, atomic = check_source(source)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SKILL / "Pairing.tla", out / "Pairing.tla")
    shutil.copy2(SKILL / "pairing.view.mjs", out / "pairing.view.mjs")
    (out / "Pairing.cfg").write_text("INIT Init\nNEXT Next\nCONSTANT AtomicConsume = " + ("TRUE" if atomic else "FALSE") + "\nINVARIANT AtMostOneRedemption\n", encoding="utf-8")
    command = ["java", "-Xmx256m", "-XX:+UseParallelGC", "-cp", str(jar()), "tlc2.TLC", "-workers", "1", "-fp", "0", "-seed", "1", "-config", "Pairing.cfg", "Pairing.tla"]
    with (out / "tlc.out").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=45)
    text = (out / "tlc.out").read_text(encoding="utf-8")
    if result.returncode == 0 and "Model checking completed. No error has been found." in text:
        outcome = "pass"
    elif "Invariant AtMostOneRedemption is violated" in text:
        outcome = "violation"
    else:
        raise RuntimeError(f"TLC did not complete correctly; inspect {out / 'tlc.out'}")
    reproduction = replay(nodes, atomic)
    assert (reproduction["accepted"] <= 1) == (outcome == "pass"), "Model/replay disagree; do not report a product bug"
    summary = {"outcome": outcome, "atomic_consume": atomic, "replay": reproduction, "source": str(source_path), "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "model_sha256": hashlib.sha256((out / "Pairing.tla").read_bytes()).hexdigest(), "tlc_version": "1.7.4", "tlc_sha256": JAR_SHA256, "command": command, "bounds": "2 requests, 1 code generation, all 4 match combinations, live or expired, fixed clock; no rotation", "evidence": str(out)}
    write_json(out / "result.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO / "server.py")
    parser.add_argument("--self-test", action="store_true", help="Check baseline, break a scratch copy's lock, replay failure, then recheck baseline")
    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 1000000)
    out = REPO / "tmp/verification/pairing-formal" / stamp
    if not args.self_test:
        return 0 if check(args.source.resolve(), out) == "pass" else 1
    original = args.source.read_text(encoding="utf-8")
    assert check(args.source.resolve(), out / "baseline") == "pass"
    nodes = functions(original)
    broken = without_lock(nodes["_pair_code_matches"])
    lines = original.splitlines(keepends=True)
    node = nodes["_pair_code_matches"]
    mutated = "".join(lines[:node.lineno - 1]) + ast.unparse(broken) + "\n" + "".join(lines[node.end_lineno:])
    # Scratch cleanup is owned by TemporaryDirectory. Product source is untouched.
    with tempfile.TemporaryDirectory(prefix="pliny-pairing-mutation-") as scratch:
        path = Path(scratch) / "server.py"
        path.write_text(mutated, encoding="utf-8")
        assert check(path, out / "without-lock") == "violation"
    assert check(args.source.resolve(), out / "restored") == "pass"
    assert args.source.read_text(encoding="utf-8") == original
    write_json(out / "self-test.json", {"baseline": "pass", "without_lock": "violation with 2 real acceptances", "restored": "pass", "scratch_cleaned": not Path(scratch).exists(), "product_source_unchanged": True})
    print(f"Self-test passed. Evidence: {out}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, AssertionError, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
