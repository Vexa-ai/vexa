# scripts/pytest-shard — one shard of a package's pytest run, for gate:python's CI matrix

`pytest_shard.py` is a pytest plugin that gate:python loads only for a `<dir>@<k>/<n>` selection
(`node scripts/gates.mjs python core/agent@2/4`). The `python` matrix in `.github/workflows/gates.yml`
uses it to split a package too slow for one job. Each shard collects the package's whole population
and keeps the test files whose path hashes to it, so shards 1..n partition what pytest collected.
`node scripts/gates.mjs python` with no selection never loads it.

`scripts/python-matrix.test.mjs` holds the matrix to the population: every package in exactly one
group, and every shard set complete.
