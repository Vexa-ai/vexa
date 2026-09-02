---
template: true                    # THE SHAPE OF an entity, never one:
                                  # hidden from every tree, never listed,
                                  # never cited, never a chip
type: person                      # required
id: <slug>                        # required — kebab-case, matches the filename
title: <Full Name>                # required — what [[wikilinks]] resolve to
description: <one line: who they are and why they matter here>
self: true                        # optional — set on the ONE person entity that is the
                                  # workspace's owner (the user); every other person omits it
role: <their role>                # optional
company: <Company>                # optional — use the company's title so [[Company]] links
resource: <url>                   # optional — a profile or source you can cite
tags: [<tag>, <tag>]              # optional
---

# <Full Name>

<One paragraph: what this person does in relation to this workspace. Written to be useful mid-meeting
— what someone would need to know the moment their name comes up.>

## Notes
- <What they care about, how they decide, what they have asked for — one fact per line.>

## Mentioned in
- [[<Meeting title>]]

## Related

<!-- the web: link every entity this one touches, and add a link BACK from each of them -->
- [[<a neighbouring entity>]] — <one clause on the relation>
