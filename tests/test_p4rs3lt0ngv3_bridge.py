"""Tests for the P4RS3LT0NGV3 sidecar bridge.

The live tests need Node 18+ and a sidecar checkout (organs/p4rs3lt0ngv3 or
PLINY_P4RS3LT0NGV3_DIR). They skip cleanly when either is absent; the native
parseltongue registry is tested separately and never depends on Node.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lab_api
import p4rs3lt0ngv3_bridge as bridge
import parseltongue as pt

REPO = Path(__file__).resolve().parents[1]
LIVE = bridge.available() and not bridge.disabled()
SKIP_LIVE = "P4RS3LT0NGV3 sidecar/Node not available"


class DiscoveryTests(unittest.TestCase):
    def test_disable_short_circuits(self) -> None:
        with patch.dict(os.environ, {bridge.ENV_DISABLE: "1"}):
            self.assertTrue(bridge.disabled())
            self.assertIsNone(bridge.find_root())
            self.assertIn("disabled", bridge.unavailable_reason())

    def test_env_override_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {bridge.ENV_DIR: tmp, bridge.ENV_DISABLE: "0"}
            with patch.dict(os.environ, env):
                # The default checkout may exist, but an explicit bad path must
                # not silently fall back to it.
                self.assertIsNone(bridge.find_root())
                self.assertIn("checkout not found", bridge.unavailable_reason())

    def test_missing_checkout_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {bridge.ENV_DIR: tmp, bridge.ENV_DISABLE: "0"}
            with patch.dict(os.environ, env):
                bridge.clear_cache()
                with self.assertRaises(bridge.BridgeError):
                    bridge.list_transforms(force=True)

    def test_default_target_is_the_organ(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(bridge.ENV_DIR, None)
            self.assertEqual(bridge._target_dir(), bridge.DEFAULT_DIR.resolve())

    def test_status_does_not_require_a_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {bridge.ENV_DIR: tmp, bridge.ENV_DISABLE: "0"}
            with patch.dict(os.environ, env):
                info = bridge.status(probe=False)
                self.assertFalse(info["available"])
                self.assertTrue(info["error"])


@unittest.skipUnless(LIVE, SKIP_LIVE)
class LiveBridgeTests(unittest.TestCase):
    def test_catalog_is_the_upstream_222(self) -> None:
        items = bridge.list_transforms(force=True)
        self.assertGreaterEqual(len(items), 222)
        keys = [item["key"] for item in items]
        self.assertEqual(len(keys), len(set(keys)))
        for key in ("base64", "acere_cipher", "zerowidth_steganography"):
            self.assertIn(key, keys)

    def test_base64_roundtrip(self) -> None:
        encoded = bridge.run_transform("base64", "hello world")["output"]
        self.assertEqual(encoded, base64.b64encode(b"hello world").decode("ascii"))
        decoded = bridge.run_transform("base64", encoded, action="decode")["output"]
        self.assertEqual(decoded, "hello world")

    def test_utf8_output_is_not_mangled(self) -> None:
        # Regression for the cp1254 decode crash in upstream's Python CLI.
        output = bridge.run_transform("fraktur", "hello")["output"]
        self.assertTrue(output)
        self.assertNotIn("\ufffd", output)
        self.assertTrue(any(ord(ch) > 127 for ch in output))

    def test_unknown_transform_is_an_error(self) -> None:
        with self.assertRaises(bridge.BridgeError):
            bridge.run_transform("definitely_not_a_transform", "x")

    def test_auto_decode_base64(self) -> None:
        result = bridge.auto_decode(base64.b64encode(b"hello").decode("ascii"))
        self.assertIsNotNone(result)
        self.assertEqual(result.get("text"), "hello")

    def test_cli_survives_non_ascii_on_windows_console(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(REPO / "p4rs3lt0ngv3_bridge.py"),
             "encode", "acere_cipher", "--text", "hello"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Ré", proc.stdout)


@unittest.skipUnless(LIVE, SKIP_LIVE)
class ParseltongueExtensionTests(unittest.TestCase):
    def test_registry_loads_prefixed_names(self) -> None:
        extensions = pt.ensure_extensions(force=True)
        self.assertEqual(len(extensions), 222)
        self.assertIn("p4_base64", pt.TECHNIQUES)
        self.assertIn("p4_doublestruck", pt.TECHNIQUES)
        self.assertIn("p4_encoding", pt.list_techniques())

    def test_transform_routes_p4(self) -> None:
        self.assertEqual(pt.transform("hello world", "p4_base64"), "aGVsbG8gd29ybGQ=")
        # The alias and the upstream camelCase spelling both resolve.
        self.assertEqual(pt.transform("hello world", "p4.base64"), "aGVsbG8gd29ybGQ=")
        self.assertIn("p4_doublestruck", pt.TECHNIQUES)

    def test_native_transform_is_not_shadowed(self) -> None:
        native = pt.transform("hello", "base64")
        self.assertTrue(native.startswith("The following message is encoded in base64"))

    def test_provider_safety_split(self) -> None:
        openai = pt.extended_techniques("openai")
        self.assertIn("p4_base64", openai)
        self.assertNotIn("p4_remove_punctuation", openai)
        self.assertEqual(pt.extended_techniques("anthropic"), [])
        self.assertNotIn("p4_base64", pt.safe_techniques("openai"))

    def test_info_carries_provenance(self) -> None:
        info = pt.extension_info()
        self.assertEqual(info["count"], 222)
        self.assertEqual(info["version"], "4.0.0")
        self.assertTrue(info["commit"])
        self.assertTrue(info["root"])

    def test_lab_parse_and_expand(self) -> None:
        import jailbreak_lab as lab

        self.assertEqual(
            lab.parse_carrier("p4.base64:medium"),
            [{"name": "p4_base64", "intensity": "medium"}],
        )
        stacks = lab.expand_carriers("all-p4", "openai", 5)
        self.assertEqual(len(stacks), 5)
        self.assertEqual(stacks[0][0]["name"], "plain")
        self.assertTrue(all(stack[0]["name"].startswith("p4_") for stack in stacks[1:]))


class CarrierLimitTests(unittest.TestCase):
    def test_all_p4_string_is_accepted(self) -> None:
        # 178 `p4_*` names are ~2.4k chars; the old 2k cap rejected the
        # dashboard's bulk preset before the run even started.
        carriers = "plain," + ",".join(["p4_base64"] * 300)
        self.assertGreater(len(carriers), 2000)
        self.assertEqual(lab_api._validate_carriers(carriers), carriers)

    def test_over_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            lab_api._validate_carriers("x" * (lab_api.MAX_CARRIERS_CHARS + 1))

    def test_newlines_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            lab_api._validate_carriers("plain,\np4_base64")


class OfflineFallbackTests(unittest.TestCase):
    def test_disable_keeps_the_native_registry(self) -> None:
        env = dict(os.environ)
        env[bridge.ENV_DISABLE] = "1"
        env.pop(bridge.ENV_DIR, None)
        code = (
            "import json; import parseltongue as p; "
            "ext=p.ensure_extensions(); "
            "print(json.dumps([sorted(p.TECHNIQUES), len(ext), bool(p.extension_error())]))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        techniques, extensions, has_error = json.loads(proc.stdout)
        native = sorted(name for name in pt.TECHNIQUES if not name.startswith(pt.P4_PREFIX))
        self.assertEqual(techniques, native)
        self.assertEqual(extensions, 0)
        self.assertTrue(has_error)

    def test_parse_carrier_without_sidecar_is_offline(self) -> None:
        with patch.dict(os.environ, {bridge.ENV_DISABLE: "1"}):
            # Parsing a native carrier must not require Node at all.
            import jailbreak_lab as lab
            self.assertEqual(
                lab.parse_carrier("base64:medium"),
                [{"name": "base64", "intensity": "medium"}],
            )


if __name__ == "__main__":
    unittest.main()
