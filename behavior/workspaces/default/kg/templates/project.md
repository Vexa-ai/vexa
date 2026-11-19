---
template: true                    # THE SHAPE OF an entity, never one:
                                  # hidden from every tree, never listed,
                                  # never cited, never a chip
type: project                     # required
id: <slug>                        # required — kebab-case, matches the filename
title: <Project name>             # required
description: <one line: what it is for and whether it is moving>
resource: <url>                   # optional — the repo, the board, the doc you can cite
tags: [<tag>, <tag>]              # optional
---

# <Project name>

<One line: what this is for. Not the plan — the point.>

## What it is

- <Scope, and what is deliberately not in it.>

## Who

- [[<Person>]] — <what they own here>

## Status

- <Where it actually is, dated. "In progress" is not a status; "the migration is written, the
  cutover is not scheduled" is.>

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
