- **Three fixes from live use (#1783).** An entity chip whose name has accented letters
  (`[[mühlemann+popp AG]]`) opens the entity's page instead of a page that does not exist. A Gmail
  reply draft may leave the subject out; it takes "Re: <original subject>". Deleting a meeting whose
  bot is still in it or finishing no longer fails: the bot is asked to leave, the answer says the
  deletion is scheduled, and the transcript and recordings are deleted when the meeting ends.
