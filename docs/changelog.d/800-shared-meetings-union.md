- **meeting-api: shared-meetings list no longer melts the database under load (#800).** The
  meetings list combined owner, transcript-share, and workspace access in one `OR`, which planned
  as a backward walk of the whole `created_at` index — 100s+ per call for users with few or old
  meetings, enough concurrent calls to saturate the connection pool (this rolled back a hosted
  0.12.12 production cutover). The three access branches now run as an indexed `UNION` (each with
  its own top-N scan) plus supporting indexes; worst-case calls drop from hundreds of seconds to
  sub-millisecond with identical results. Deployers: build the three new `meetings` indexes
  `CONCURRENTLY` before rolling the image on a large live table.
