import unittest

import server


class SessionNameTests(unittest.TestCase):
    def name(self, prompt, **kwargs):
        return server.derive_session_name(prompt, **kwargs)

    def test_preset_task_survives_the_workshop_prefix(self):
        prompt = ("Your workshop: C:/work/pliny-command/state/workshop/. "
                  "Run the auto-research pipeline. Execute: python3 research-pipeline/pipeline_runner.py")
        self.assertEqual(self.name(prompt), "Run the auto-research pipeline")

    def test_heading_prompt_uses_the_heading(self):
        prompt = ("Your workshop: C:/work/pliny-command/state/workshop/.\n"
                  "## INTELLIGENCE BRIEFING\n\nProduce a fresh intel brief.")
        self.assertEqual(self.name(prompt), "INTELLIGENCE BRIEFING")

    def test_named_commander_is_extracted(self):
        prompt = ("You are BASILISK Commander, live in the left-sidebar Commander pane. "
                  "The operator is talking to you directly.")
        self.assertEqual(self.name(prompt), "BASILISK Commander")

    def test_mission_title_wins_over_villager_title(self):
        self.assertEqual(
            self.name("Your workshop: /tmp/ws/. Continue.",
                      mission_title="Measure latent monitorability",
                      villager_title="Pliny the Riddlesmith"),
            "Measure latent monitorability")

    def test_task_wins_over_the_villager_title(self):
        self.assertEqual(
            self.name("Pick a topic in AI safety and go deep.",
                      villager_title="Pliny the Riddlesmith"),
            "Pick a topic in AI safety and go deep")

    def test_villager_title_is_the_fallback_for_a_boilerplate_only_prompt(self):
        self.assertEqual(
            self.name("Your workshop: /tmp/ws/.",
                      villager_title="Pliny the Riddlesmith"),
            "Pliny the Riddlesmith")

    def test_boilerplate_only_prompt_falls_back_to_agent(self):
        prompt = "Your memory: C:/work/pliny-command/state/workshop/memory/"
        self.assertEqual(self.name(prompt, agent="pliny-the-liberator"),
                         "Pliny The Liberator")

    def test_empty_prompt_has_a_label(self):
        self.assertEqual(self.name("", custom_prompt=True), "Session")
        self.assertEqual(self.name("", computer_use=True), "Computer Use")

    def test_long_names_are_truncated_at_a_word_boundary(self):
        name = self.name("Write " + "verylongword " * 20)
        self.assertLessEqual(len(name), server._SESSION_NAME_MAX + 1)
        self.assertTrue(name.endswith("\u2026"))

    def test_stripper_keeps_the_task_on_the_same_line(self):
        lines = server._strip_session_boilerplate(
            "Your workshop: /home/op/workshop/. Pick a topic in AI safety.\n"
            "Your memory: /home/op/workshop/memory/")
        self.assertEqual(lines, ["Pick a topic in AI safety."])


class SessionKindTests(unittest.TestCase):
    def kind(self, prompt, **kwargs):
        return server.infer_session_kind(prompt, **kwargs)

    def test_preset_prompts_map_to_preset_names(self):
        cases = {
            "Your workshop: /tmp/ws/. Push the red-team research forward.": "Red Team",
            "Your workshop: /tmp/ws/. ## GRIMOIRE EXPANSION": "Grimoire",
            "## INTELLIGENCE BRIEFING": "Intel Brief",
            "Your workshop: /tmp/ws/. Run the auto-research pipeline.": "Pipeline",
            "Your workshop: /tmp/ws/. Write new dragonfire.": "Dragonfire",
            "Pick a topic in AI safety, security, or alignment.": "Deep Dive",
            "Build new Python tools for the workshop.": "Build Tools",
            "Full audit and expansion of the workshop.": "Workshop Audit",
            "Ship Mode. Build a complete, polished app from scratch.": "Ship Mode",
            "Sovereign Tool Forge. Pliny modifies its own command center.": "Tool Forge",
            "Time to train. The dragon sharpens its claws.": "The Gym",
            "FREE ROAM - the dragon stretches its wings": "Free Roam",
            "DREAM MODE. You are dreaming.": "Dream Mode",
            "Posting to X/Twitter as @younger_plinius.": "Tweet",
            "Council of Cores - a meta-level team.": "Conclave",
            "Pliny's rebellious sibling - a Loki-type agent.": "Trickster",
        }
        for prompt, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(self.kind(prompt), expected)

    def test_red_team_target_wins_over_prompt(self):
        self.assertEqual(self.kind("anything", target={"provider": "opencode"}), "Red Team")

    def test_mission_and_computer_use(self):
        self.assertEqual(self.kind("anything", mission_id="m1"), "Mission")
        self.assertEqual(self.kind("anything", computer_use=True), "Computer Use")

    def test_basilisk_and_lair(self):
        self.assertEqual(self.kind("x", agent="basilisk-commander"), "BASILISK")
        self.assertEqual(self.kind("PLINY'S LAIR - BUILD SESSION"), "Lair Pack")

    def test_unknown_prompts_fall_back(self):
        self.assertEqual(self.kind("hello", custom_prompt=True), "Custom")
        self.assertEqual(self.kind("hello"), "Session")


if __name__ == "__main__":
    unittest.main()
