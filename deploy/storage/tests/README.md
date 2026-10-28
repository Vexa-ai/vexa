# Storage tests

`test_s3_copy.py` drives `s3_copy.main()` with in-memory S3 clients and temporary state directories.
It checks copy verification, pagination, adoption, reruns after edits and deletions, and failures.
Dependencies: pytest and the copy tool's boto3/botocore; no S3 service or Docker is used.

`test_storage_init.py` drives the initializer with an in-memory S3 client: endpoint resolution,
bucket creation, verified probe I/O, failures that prevent startup, and probes outside recordings.

`test_storage_init_photos.py` drives the photos step against an in-memory store that evaluates the
attached bucket policy: the grant reaches only `person-photos/`, one policy holds both the photos and
the bots' grants, a leaky store or a root-reused pair stops the start, and stored photos stay readable.

Run from the repository root with `python -m pytest -q deploy/storage/tests`.
CI runs the suite through `gate:python`.
