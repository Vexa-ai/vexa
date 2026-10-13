# virtual-camera/scripts

[`check-isolation.js`](check-isolation.js) — `gate:isolation` for this brick: every import must stay
inside the package (intra-package, a Node builtin, or a declared dep). The camera is PAGE code with
zero external imports; DOM globals are ambient, not imports.
