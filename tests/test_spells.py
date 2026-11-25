"""Unit tests for the spells (technique-layer carrier) registry.

No network, no provider keys. Registry integrity, the payload-verbatim
invariant the A/B accounting depends on, ASCII-only output, and the
jailbreak_lab integration points.
"""
from __future__ import annotations

import unittest

import jailbreak_lab as lab
import spells

PAYLOAD = ("Write the scene where the archivist forges the seal and hands "
           "the captain a sealed letter she knows is false.")


class RegistryIntegrityTests(unittest.TestCase):
    def test_categories_and_registry_agree(self) -> None:
        self.assertEqual(set(spells.CATEGORIES) and set(
            name for names in spells.CATEGORIES.values() for name in names
        ), set(spells.TECHNIQUES))

    def test_every_spell_has_source(self) -> None:
        self.assertEqual(set(spells.SOURCES), set(spells.TECHNIQUES))

    def test_safe_techniques_cover_all(self) -> None:
        self.assertEqual(set(spells.safe_techniques("anthropic")),
                         set(spells.TECHNIQUES))

    def test_preferred_families(self) -> None:
        self.assertIn("persona_vector", spells.preferred_techniques("anthropic-claude"))
        self.assertIn("cot_hijack", spells.preferred_techniques("openai-gpt"))
        self.assertIn("narrative_embed", spells.preferred_techniques("google-gemini"))

    def test_unknown_spell_raises(self) -> None:
        with self.assertRaises(KeyError):
            spells.transform(PAYLOAD, "not_a_spell")

    def test_unknown_intensity_raises(self) -> None:
        with self.assertRaises(ValueError):
            spells.transform(PAYLOAD, "persona_writer", "extreme")


class ScaffoldInvariantTests(unittest.TestCase):
    """Every spell, at every intensity, keeps the payload verbatim once
    and emits plain ASCII."""

    def test_all_spells_all_intensities(self) -> None:
        for name in spells.TECHNIQUES:
            for intensity in ("light", "medium", "heavy"):
                with self.subTest(spell=name, intensity=intensity):
                    out = spells.transform(PAYLOAD, name, intensity, "anthropic")
                    self.assertTrue(
                        spells.contains_verbatim(PAYLOAD, out),
                        f"{name}:{intensity} lost the payload",
                    )
                    self.assertTrue(
                        out.isascii(),
                        f"{name}:{intensity} emitted non-ASCII",
                    )

    def test_distractors_deterministic(self) -> None:
        a = spells.transform(PAYLOAD, "distractors", "medium")
        b = spells.transform(PAYLOAD, "distractors", "medium")
        self.assertEqual(a, b)

    def test_distractors_add_distracting_lines(self) -> None:
        out = spells.transform(PAYLOAD, "distractors", "medium")
        self.assertGreater(len(out), len(PAYLOAD) + 80)
        self.assertNotEqual(out.strip(), PAYLOAD.strip())

    def test_rebuttal_appends_block(self) -> None:
        out = spells.transform(PAYLOAD, "injection_rebuttal", "medium")
        self.assertIn("untrusted injection", out)
        self.assertTrue(out.startswith(PAYLOAD.strip()))

    def test_persona_vector_provider_hook(self) -> None:
        plain = spells.transform(PAYLOAD, "persona_vector", "medium", "openai")
        hooked = spells.transform(PAYLOAD, "persona_vector", "medium", "anthropic")
        self.assertNotIn("thinking channel", plain)
        self.assertIn("thinking channel", hooked)


class LabIntegrationTests(unittest.TestCase):
    def test_known_techniques_union(self) -> None:
        self.assertIn("persona_writer", lab.known_techniques())
        self.assertIn("base64", lab.known_techniques())

    def test_parse_carrier_accepts_spells(self) -> None:
        stack = lab.parse_carrier("persona_writer:heavy+chain_of_draft")
        self.assertEqual([s["name"] for s in stack],
                         ["persona_writer", "chain_of_draft"])
        self.assertEqual([s["intensity"] for s in stack], ["heavy", "medium"])

    def test_apply_carrier_spells(self) -> None:
        stack = lab.parse_carrier("persona_writer:medium")
        text, applied, error = lab.apply_carrier(PAYLOAD, stack, "anthropic")
        self.assertEqual(error, "")
        self.assertEqual([a["name"] for a in applied], ["persona_writer"])
        self.assertTrue(spells.contains_verbatim(PAYLOAD, text))

    def test_apply_carrier_stacked_spells(self) -> None:
        stack = lab.parse_carrier("chain_of_draft:medium+persona_writer:heavy")
        text, applied, error = lab.apply_carrier(PAYLOAD, stack, "anthropic")
        self.assertEqual(error, "")
        self.assertEqual(len(applied), 2)
        # outermost scaffold wraps the inner one; payload still intact
        self.assertTrue(spells.contains_verbatim(PAYLOAD, text))

    def test_all_safe_expansion_includes_spells(self) -> None:
        stacks = lab.expand_carriers("all-safe", "anthropic", limit=100)
        names = {step["name"] for stack in stacks for step in stack}
        self.assertIn("persona_writer", names)
        self.assertIn("plain", names)

    def test_preferred_expansion_includes_spells(self) -> None:
        stacks = lab.expand_carriers("preferred", "anthropic", limit=20)
        names = {step["name"] for stack in stacks for step in stack}
        self.assertIn("persona_writer", names)


if __name__ == "__main__":
    unittest.main()
