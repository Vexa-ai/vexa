# tests/fixtures — two transcripts in the DNA corpus's shape

Small stand-ins for `~/dna-fixtures/`, in exactly the shape the real library uses: a `meeting`
block (`title`, `platform`, `native_meeting_id`, `occurrence`, `participants` as display names
with an org in parentheses) and `segments` keyed `t` / `end` / `speaker` / `text`.

**They are here so the suite never reads the rig's home directory.** `~/dna-fixtures` is a
deployment fact: a test that read it would pass on bbb and fail everywhere else, and would quietly
start measuring whatever somebody last left in that directory. Three turns is enough — the tests
assert over the recipe and the derived addresses, never over the words.

The org in parentheses is deliberate: `engine.attendee_address` has to drop it
("Olga Avramenko (Sony Pictures Imageworks)" → `olga-avramenko@rehearse.test`), and a fixture
without one would let that regress unnoticed.
