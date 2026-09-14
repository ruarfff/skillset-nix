"""Check release numbering without creating commits, tags, or remote writes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))
from next_tag import next_tag, release_plan


class ReleaseTest(unittest.TestCase):
    def test_release_numbering_and_reruns(self) -> None:
        cases = [
            ([], [], "v0.1.0"),
            (["v0.1.0"], [], "v0.1.1"),
            (["v0.1.9", "v0.1.10", "v0.1.2"], [], "v0.1.11"),
            (["v1.9.9", "v2.0.0"], [], "v2.0.1"),
            (["notes", "v9.0.0-rc.1", "v8.0.0+build", "v01.2.3"], [], "v0.1.0"),
            (["v0.1.0", "v9.0.0-rc.1"], ["preview"], "v0.1.1"),
            (["v0.1.0"], ["v0.1.0"], None),
            (["v0.1.0", "v0.1.1"], ["preview", "v0.1.0"], None),
        ]
        for tags, head_tags, expected in cases:
            with self.subTest(tags=tags, head_tags=head_tags):
                self.assertEqual(next_tag(tags, head_tags), expected)

    def test_release_plan_recovers_after_tag_creation(self) -> None:
        self.assertEqual(release_plan([], []), ("v0.1.0", True))
        self.assertEqual(
            release_plan(["v0.1.0"], ["preview", "v0.1.0"]),
            ("v0.1.0", False),
        )
        self.assertEqual(
            release_plan(["v1.0.0", "v2.0.0"], ["v1.0.0", "v2.0.0"]),
            ("v2.0.0", False),
        )


if __name__ == "__main__":
    unittest.main()
