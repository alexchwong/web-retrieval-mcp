# Third-party notices

webSpell itself is MIT licensed (see `LICENSE`). The webSpell wheel contains only
webSpell's own code. This file covers what the **Docker image** and the **release
ZIP** install alongside it: both install webSpell with the `pdf` extra.

## pypdfium2 and PDFium (`webSpell[pdf]`)

The `pdf` extra installs [pypdfium2](https://github.com/pypdfium2-team/pypdfium2),
which bundles a binary build of Google's [PDFium](https://pdfium.googlesource.com/pdfium/)
and the libraries PDFium is built with.

- pypdfium2 licence metadata: `BSD-3-Clause, Apache-2.0, dependency licenses`.
- The full licence texts ship inside pypdfium2's own wheel, in its
  `pypdfium2-<version>.dist-info/licenses/` directory:
  - `LICENSES/` — pypdfium2's own licences (Apache-2.0, BSD-3-Clause, CC-BY-4.0).
  - `BUILD_LICENSES/` — PDFium (`pdfium.txt`), the pdfium-binaries build
    (`pdfium-binaries.txt`) and every library compiled into the binary
    (for example FreeType, HarfBuzz, ICU, libjpeg-turbo, OpenJPEG, libpng,
    libtiff, Little CMS, zlib, abseil).

In the Docker image these files are under the Python `site-packages` directory,
for example:

```bash
docker compose exec web-retrieval sh -c \
  'ls "$(python -c "import site; print(site.getsitepackages()[0])")"/pypdfium2-*.dist-info/licenses'
```

webSpell does not modify pypdfium2 or PDFium.

## Other runtime packages

The core requirements (`httpx`, `trafilatura` and their dependencies) and the
`server` extra (`fastmcp` and its dependencies) are installed from their own
wheels, each of which carries its own licence metadata and files.
