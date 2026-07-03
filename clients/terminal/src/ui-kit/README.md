# ui-kit

Shared presentational primitives for the terminal — currently the `Icon` component (the activity-bar /
inline glyph set). Surfaces and the workbench consume these; they hold no state and depend on no service
(pure view). Theme comes from the prototype CSS variables (`--t1`/`--accent`/`--panel`/…), not props.
