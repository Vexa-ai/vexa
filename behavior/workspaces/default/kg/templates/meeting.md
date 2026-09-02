---
template: true                    # THE SHAPE OF an entity, never one:
                                  # hidden from every tree, never listed,
                                  # never cited, never a chip
type: meeting                     # required
id: <yyyy-mm-dd-slug>             # required — matches the filename
title: <Meeting title>            # required
date: <YYYY-MM-DD>                # required — written in full elsewhere in prose
participants: [<Person>, <Person>]
tags: [<tag>]
---

# <Meeting title>

<One line on what the meeting settled.>

## Decided
- <Decision, and what it changes.>

## Committed
- [[<Person>]] — <what they will do, by when; a condition attached to it is part of it.>

## Open
- <What is unresolved, and who owns resolving it.>

## Related

<!-- the web: link every entity this one touches, and add a link BACK from each of them -->
- [[<a neighbouring entity>]] — <one clause on the relation>
