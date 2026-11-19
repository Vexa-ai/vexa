- **References to anything, checked for whoever opens them (#1783).** Every workspace, chat, page,
  entity, meeting, flow and sensor has one link, `/r/<kind>/<id>`, and one chip, `[[kind:id|label]]`.
  A reference opens for a person who has access and says **No access** with **Request access** to
  anyone else, without showing the name. Pasting a reference link into the composer makes it a chip;
  the agent has `ref_resolve`. A `[[Title]]` chip is found by the server, so names with accents open
  their page. See [Pages](/minutes/pages).
