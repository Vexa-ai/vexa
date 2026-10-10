- **An authenticated bot's browser navigates only to the meeting's host and the platform's own
  domains (#1784).** Pages, popups and frames elsewhere are refused (and a redirect that lands
  elsewhere is blanked), each logged; scripts, images and media load as before.
