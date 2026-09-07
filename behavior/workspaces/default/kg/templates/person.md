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

<One line: what this person does in relation to this workspace — what someone would need to know
the moment their name comes up.>

## Role and organisation

- Role: <their role>
- Company: [[<Company>]]

## What they care about

- <What they care about, how they decide, what they have asked for — one fact per line.>

## How we relate

- <What we are to each other: customer, maintainer, counterparty, colleague. What is owed either way.>

## Connected

<!-- the web: link every entity this one touches, and add a link BACK from each of them -->
- [[<a neighbouring entity>]] — <one clause on the relation>

## Sources
- <where each of the above was said or read>

## Open questions
- <what we would need to know, written as the question, never guessed at on the page>

## Timeline

### <YYYY-MM-DD>
- <what was learned that day, with its source>
