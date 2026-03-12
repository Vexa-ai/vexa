"""Tests for concurrent bot launch race condition fix.

Verifies that the bot launch endpoint acquires a row-level lock (SELECT ... FOR UPDATE)
on the user row before checking the concurrent bot count, preventing the race condition
where two concurrent requests both pass the count check.
"""

import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


class TestForUpdateLockPresent(unittest.TestCase):
    """Verify that the bot launch code path includes FOR UPDATE locking."""

    SOURCE_FILE = os.path.join(_HERE, os.pardir, "app", "main.py")

    def _read_source(self) -> str:
        with open(self.SOURCE_FILE, "r") as f:
            return f.read()

    def test_with_for_update_in_concurrency_check(self):
        """The user row must be locked with FOR UPDATE before the count query."""
        source = self._read_source()
        # Find the concurrency limit section
        marker = "Fast-fail concurrency limit check"
        idx = source.find(marker)
        self.assertNotEqual(idx, -1, f"Could not find '{marker}' comment in source")

        # Extract the block after the marker up to the INSERT (next ~40 lines)
        block = source[idx : idx + 1500]

        # Verify with_for_update() appears BEFORE the count query
        lock_pos = block.find("with_for_update()")
        count_pos = block.find("select(func.count())")
        self.assertNotEqual(lock_pos, -1, "with_for_update() not found in concurrency check block")
        self.assertNotEqual(count_pos, -1, "count query not found in concurrency check block")
        self.assertLess(
            lock_pos,
            count_pos,
            "with_for_update() must appear BEFORE the count query to hold the lock during the check",
        )

    def test_lock_targets_user_row(self):
        """The FOR UPDATE lock must target the User model, not another table."""
        source = self._read_source()
        marker = "Fast-fail concurrency limit check"
        idx = source.find(marker)
        block = source[idx : idx + 600]

        # The lock statement should select from User
        self.assertIn("select(User)", block, "FOR UPDATE lock must target User model")
        self.assertIn("with_for_update()", block)


if __name__ == "__main__":
    unittest.main()
