#!/usr/bin/env python3
"""Unit tests for the evidence algebra and the pseudonymizer.

All names here are invented. Real participant names never enter this tree — the
production cases they stand in for are described in
`calibration/REPORT.md` in pseudonyms only.

Run: python3 -m unittest discover -s core/meetings/eval/misattribution
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pseudonymize import build_name_map, pseudonym_for, redact  # noqa: E402
from score import score  # noqa: E402


def seg(sid, speaker, text=""):
    return {"segment_id": sid, "speaker": speaker, "text": text, "start": 0.0}


def flag(sid, signal, named):
    return {
        "segment_id": sid, "signal": signal, "named": named,
        "direction": "not_speaker" if signal == "vocative" else "is_speaker",
        "quote_span": "", "t_start": 0.0,
    }


class TestPseudonymize(unittest.TestCase):
    def test_roster_spellings_of_one_human_collapse(self):
        # The defect this guards, observed in production: the DOM label, the
        # calendar attendee and the email local-part arrive as three spellings
        # of one person. Splitting them across pseudonyms makes every vocative
        # test silently miss — the judge flags P4 while the label says P1 and
        # the scorer sees no contradiction at all.
        nm = build_name_map(["Nadai Okonjo", "Sam", "nadia"])
        self.assertEqual(nm.to_pseudo["Nadai Okonjo"], nm.to_pseudo["nadia"])
        self.assertNotEqual(nm.to_pseudo["Sam"], nm.to_pseudo["nadia"])

    def test_in_text_names_are_redacted(self):
        nm = build_name_map(["Sam", "Nadia Okonjo"])
        out = redact("Yeah, thanks, Sam. Nice to meet Nadia.", nm)
        self.assertNotIn("Sam", out)
        self.assertNotIn("Nadia", out)
        self.assertIn(nm.to_pseudo["Sam"], out)

    def test_given_name_extension_matches(self):
        nm = build_name_map(["Dani", "Bob"])
        self.assertIn(nm.to_pseudo["Dani"], redact("okay Danielle I need to jump off", nm))

    def test_common_words_survive_the_fuzzy_path(self):
        # Over-eager redaction corrupts the judge's input for no gain.
        nm = build_name_map(["Willa Reyes", "Bob"])
        out = redact("I think that will work and we should present it", nm)
        self.assertIn("will", out)
        self.assertIn("present", out)

    def test_label_maps_through_a_misspelling(self):
        nm = build_name_map(["Nadia Okonjo", "Sam"])
        self.assertEqual(pseudonym_for("Nadai Okonjo", nm), nm.to_pseudo["Nadia Okonjo"])

    def test_off_roster_label_has_no_pseudonym(self):
        nm = build_name_map(["Nadia Okonjo", "Sam"])
        self.assertIsNone(pseudonym_for("Zeta Notetaker", nm))


class TestScore(unittest.TestCase):
    def setUp(self):
        self.nm = build_name_map(["Sam", "Nadia Okonjo"])  # Sam=P1, Nadia=P2

    def test_vocative_naming_the_track_label_is_a_contradiction(self):
        segs = [seg("csrc-201:1:0", "Sam"), seg("csrc-201:2:0", "Sam")]
        res = score(segs, [flag("csrc-201:1:0", "vocative", "P1")], self.nm)
        t = res["tracks"][0]
        self.assertEqual(t["verdict"], "MISLABELED")
        self.assertEqual(len(t["contradictions"]), 1)
        # Two roster participants: "not P1" identifies P2 uniquely.
        self.assertEqual(t["implied_label"], "P2")

    def test_vocative_naming_the_other_party_is_not_evidence_of_error(self):
        segs = [seg("ch-0:1:0", "Sam"), seg("ch-0:2:0", "Sam")]
        res = score(segs, [flag("ch-0:1:0", "vocative", "P2")], self.nm)
        self.assertEqual(res["tracks"][0]["verdict"], "INSUFFICIENT")
        self.assertEqual(res["totals"]["contradictions"], 0)

    def test_self_id_matching_the_label_supports_it(self):
        segs = [seg("ch-1:1:0", "Sam")]
        res = score(segs, [flag("ch-1:1:0", "self_id", "P1")], self.nm)
        t = res["tracks"][0]
        self.assertEqual(t["verdict"], "CLEAN")
        self.assertEqual(len(t["supports"]), 1)

    def test_self_id_against_the_label_names_the_true_owner(self):
        segs = [seg("ch-1:1:0", "Sam")]
        res = score(segs, [flag("ch-1:1:0", "self_id", "P2")], self.nm)
        t = res["tracks"][0]
        self.assertEqual(t["verdict"], "MISLABELED")
        self.assertEqual(t["implied_label"], "P2")

    def test_support_outweighing_contradiction_does_not_flag(self):
        segs = [seg("ch-0:1:0", "Sam"), seg("ch-0:2:0", "Sam"), seg("ch-0:3:0", "Sam")]
        flags = [flag("ch-0:1:0", "vocative", "P1"),
                 flag("ch-0:2:0", "self_id", "P1"),
                 flag("ch-0:3:0", "self_id", "P1")]
        self.assertEqual(score(segs, flags, self.nm)["tracks"][0]["verdict"], "INSUFFICIENT")

    def test_a_two_way_track_swap_reproduces(self):
        # The production shape both gold fixtures come from: each track carries
        # the other party's name, and each incriminates itself.
        segs = ([seg(f"csrc-201:{i}:0", "Sam") for i in range(5)]
                + [seg(f"csrc-840:{i}:0", "Nadia Okonjo") for i in range(5)])
        flags = [flag("csrc-201:1:0", "vocative", "P1"),
                 flag("csrc-201:4:0", "vocative", "P1"),
                 flag("csrc-840:2:0", "vocative", "P2")]
        res = score(segs, flags, self.nm)
        self.assertEqual(res["totals"]["mislabeled"], 2)
        self.assertEqual(res["totals"]["contradictions"], 3)

    def test_off_roster_label_never_produces_a_contradiction(self):
        # A track labeled with a name outside the roster — a second notetaker
        # bot, say — has no pseudonym, so no vocative can contradict it.
        segs = [seg("ch-2:1:0", "Zeta Notetaker")]
        res = score(segs, [flag("ch-2:1:0", "vocative", "P1")], self.nm)
        self.assertEqual(res["tracks"][0]["verdict"], "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
