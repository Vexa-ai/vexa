# entities

The knowledge graph's typed entities, one directory per type (`<type>/<slug>.md`). Required
frontmatter: `type`, `id`, `title`; entities reference each other with `[[wikilinks]]` by title;
each type keeps an `index.md` listing what exists.

- [person](person/index.md) — people
- [company](company/index.md) — companies and organisations
- [meeting](meeting/index.md) — meetings

The SHAPE of each type lives in [`../templates/`](../templates/README.md) — copy one for the first
entity of a kind. Templates are skeletons, never knowledge: nothing in them is a real record.
