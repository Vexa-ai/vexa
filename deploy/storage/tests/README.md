# Storage copy tests

`test_s3_copy.py` drives `s3_copy.main()` with in-memory S3 clients and temporary state directories.
It checks copy verification, pagination, adoption, reruns after edits and deletions, and failures.
Dependencies: pytest and the copy tool's boto3/botocore; no S3 service or Docker is used.

Run from the repository root with `python -m pytest -q deploy/storage/tests/test_s3_copy.py`.
