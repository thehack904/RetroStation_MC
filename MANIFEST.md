# RetroStation MC v1.5.0 Beta 2 Release Manifest

Public release package contents include the application source, Linux installer, Docker support, tests, sample data, and documentation.

Release-cleanup notes:

- Public repository: `https://github.com/thehack904/RetroStation_MC`
- Generated Python/test caches and development backup files are excluded from the release archive.
- Documentation examples use reserved example-network addresses rather than private development-network addresses.
- Flask session signing uses a persistent per-install random key instead of a repository-wide hard-coded key.
