- **Release witness receipts: full image digests or no pass (#713).** The witness gate now rejects
  any `sha256:` in a receipt's deployment fields that is not followed by the full 64-hex digest
  (prefixes and trailing ellipses fail, naming the field), and the generator's skeleton asks for the
  full index + platform-image digests at fill time. The v0.12.10 receipt's deployment field is
  completed to the full values in the same change — the record of which bytes were witnessed no
  longer needs a registry lookup to be evidence.
