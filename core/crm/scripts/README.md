# CRM validation scripts

`verify_export.py EXPORT_ROOT` compiles permissions from exported source rows and
compares them with an independently supplied `expected-visibility.csv` next to
`export/`. The oracle is never passed to the importer or permission compiler.
