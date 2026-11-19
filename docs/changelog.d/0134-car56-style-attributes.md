- **The terminal's pages refuse inline style attributes.** Their Content-Security-Policy now carries
  `style-src-attr 'none'` instead of `'unsafe-inline'`, so a `style="…"` that reaches a page as
  markup no longer applies. The pages render their first frame with classes, and mermaid diagrams
  draw their styles through the CSSOM. The pages look the same. The 404 page is the terminal's own.
