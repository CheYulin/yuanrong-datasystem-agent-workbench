# scripts/lib

Compatibility shim for shared shell/Python helpers.

Canonical implementations still live in `scripts/development/lib/` during the migration. New scripts should source
`scripts/lib/<name>`; each shim forwards to the existing implementation so old callers continue to work.
