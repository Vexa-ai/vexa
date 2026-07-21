- **Teams speaker names survive the DOM's hashed-class drift (#853, #797).** Today's Teams renders
  a participant's name in a div whose only classes are minified atomic hashes (`___12zni01 …`) that
  rotate every release, so the fixed name selectors miss and the transcript goes anonymous even
  though the names are plainly on-screen. The resolver now falls back to a selector-agnostic
  structural scan — it reads the name from the tile the way a human does — after the explicit
  selectors miss, and rejects timers/UI-control words so it never invents a name.
