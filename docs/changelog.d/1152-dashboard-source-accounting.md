### Dashboard source accounting

- Retain the exact hosted Dashboard source in `services/dashboard` so the UI deployed in PROD is
  auditable from OSS main. Dashboard remains load-bearing for hosted authenticated-session and
  browser-session workflows, but is deprecated in favor of Terminal for new self-hosted installs.
  The imported implementation files are byte-identical to source commit
  `fa667cd11d95afec551437373c1bdbcaf816a3a0`, which produced the deployed immutable Dashboard
  index `sha256:57a1c4f09664972ea227c8bdf282585d80ff79c2862e0dc922b88155ac4fc9b3`.
