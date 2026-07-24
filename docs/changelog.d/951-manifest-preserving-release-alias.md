Release promotion now assigns moving image aliases by copying the authenticated
registry manifest bytes and media type exactly, then reads back all frozen
descriptors. Single-platform images can no longer be silently wrapped in a new
index, and `:latest` is checked as an unchanged negative control unless its
promotion was explicitly requested.
