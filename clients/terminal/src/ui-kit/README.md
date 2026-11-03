# ui-kit

Shared presentational primitives for the terminal — currently the `Icon` component (the activity-bar /
inline glyph set). Surfaces and the workbench consume these; they hold no state and depend on no service
(pure view). Theme comes from the prototype CSS variables (`--t1`/`--accent`/`--panel`/…), not props.

## Layout and primitives (terminal design guidelines)

The full guidelines, with "How it works" and "Why it complies", are the docs page
`docs/docs/governance/ui-design.mdx`. In this folder:

- `layout/` — the responsive shell: `shellLayout()` (pure), `useShellLayout` (the one writer of
  `vexa.shell.v1`), `Splitter`, `Sheet`, `Drawer`.
- `primitives/` — `Menu`, `KeyValue`, `Truncate`, `DateText`, `Fold`, `OverflowStrip`.
- `format/` — the one date formatter.
- `styles.css` — gathers the primitives' global stylesheets; `src/app/globals.css` imports it once.

Everything is exported from `index.tsx`, the folder's one front door (P6). Surfaces never deep-import.

The document renderers here (`Markdown`, `MdxDoc`, `docLinks`, `docImages`, `wsLinks`) read
workspaces through `../data/workspaceApi`, reached by lazy `import()`. The one act a document asks
of the shell above it, an image's **Find it**, goes through `docIntents.ts`: the ui-kit names the
intent and the composition root (`app/App.tsx`) supplies `minutes/extend`'s `postIntent` as the
door. The ui-kit imports `platform/`, `contributions/` and `data/`, and nothing above them.
