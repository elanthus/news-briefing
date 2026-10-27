import hashlib
import json
import unittest

import briefing_config
from agent_runner.positions import StoryPosition


class StoryPositionTest(unittest.TestCase):
    def test_payload_round_trip(self):
        payload = {"bucket": "excluded_topics", "section": "US News", "index": 7}
        position = StoryPosition.from_payload(payload)
        self.assertEqual(position.to_payload(), payload)
        self.assertEqual(StoryPosition.from_payload(position.to_payload()), position)

    def test_path_round_trip(self):
        for position in (StoryPosition("sections", "US News", 2), StoryPosition("excluded_topics", "a[1]", 63)):
            self.assertEqual(StoryPosition.from_path(position.path()), position)
        self.assertEqual(StoryPosition("sections", "US News", 2).path(), "topics.US News[2]")
        self.assertEqual(StoryPosition("excluded_topics", "x", 0).path(), "excluded_topics.x[0]")

    def test_bounds_rejection(self):
        good = {"bucket": "sections", "section": "s", "index": 0}
        cases = [
            ({**good, "bucket": "topics"}, "invalid integrity enum"),
            ({**good, "section": "x" * 101}, "invalid integrity string"),
            ({**good, "section": " "}, "invalid integrity string"),
            ({**good, "index": 64}, "invalid integrity count"),
            ({**good, "index": -1}, "invalid integrity count"),
            ({**good, "index": True}, "invalid integrity count"),
            ({**good, "index": "0"}, "invalid integrity count"),
            ({**good, "section": 3}, "invalid integrity string"),
            ({**good, "extra": 1}, "invalid integrity position fields"),
            ({"bucket": "sections", "section": "s"}, "invalid integrity position fields"),
            (["sections", "s", 0], "invalid integrity position fields"),
        ]
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, f"^{message}$"):
                StoryPosition.from_payload(value)
        self.assertEqual(StoryPosition.from_payload({**good, "section": "x" * 100}).section, "x" * 100)
        self.assertEqual(StoryPosition.from_payload({**good, "index": 63}).index, 63)
        for text in ("topics.s[64]", "sections.s[0]", "topics.s", "topics.[0]"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                StoryPosition.from_path(text)

    def test_ordering(self):
        positions = [StoryPosition("sections", "b", 0), StoryPosition("excluded_topics", "z", 9),
                     StoryPosition("sections", "a", 10), StoryPosition("sections", "a", 2)]
        self.assertEqual(sorted(positions), [positions[1], positions[3], positions[2], positions[0]])
        self.assertEqual(len({StoryPosition("sections", "a", 1), StoryPosition("sections", "a", 1)}), 1)

    def test_anchor_ids_are_unchanged(self):
        self.assertEqual(StoryPosition("sections", "dev_community", 0).anchor_id(), "story-066ded60e52f")
        self.assertEqual(StoryPosition("excluded_topics", "other_news", 3).anchor_id(), "story-c886de1e70e8")


if __name__ == "__main__":
    unittest.main()

    def test_control_characters_in_section_are_rejected(self):
        for section in ("a\tb", "a\x7fb", " ", "\n"):
            with self.assertRaises(ValueError):
                StoryPosition.from_payload({"bucket": "sections", "section": section, "index": 0})

    def test_json_key_order_differs_from_natural_order(self):
        two = StoryPosition("sections", "a", 2)
        ten = StoryPosition("sections", "a", 10)
        self.assertLess(two, ten)
        self.assertEqual(sorted([two, ten], key=StoryPosition.json_key), [ten, two])

    def test_anchor_id_matches_the_legacy_formula(self):
        position = StoryPosition("excluded_topics", "Dev Community", 7)
        legacy = json.dumps(position.to_payload(), sort_keys=True).encode()
        self.assertEqual(position.anchor_id(), "story-" + hashlib.sha256(legacy).hexdigest()[:12])

    def test_shipped_section_names_are_valid_positions(self):
        for section in briefing_config.load_config().sections:
            StoryPosition.from_payload({"bucket": "sections", "section": section.name, "index": 0})
