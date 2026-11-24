Placeholder so the Dockerfile's `COPY vendor/` has a source directory.
The CI vendor step was removed on 2026-07-03. chronograph now lives in this
repo at `chronograph/`. Nothing populates vendor/ any more; anything other than
this file is gitignored.
