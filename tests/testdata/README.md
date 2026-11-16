# Local TLS fixtures

The test CA is trusted only by the offline transport tests. The leaf private
key is public test material. Do not install the CA or use the key outside the
tests. The CA signing key is absent, and these files are excluded from the
installable ZIP.

To regenerate, use OpenSSL or a development certificate tool. Create a fresh
RSA test CA with critical CA basic constraints and certificate-signing key
usage. Issue two server-auth leaves sharing a fresh test key, with AKI and SKI
extensions and separate SANs for DNS `good.test` and IP `127.0.0.1`. Use a
validity period covering CI, export the public CA and leaves plus the test
server key under the existing filenames, and discard the CA signing key.
Verify with `openssl verify -x509_strict -CAfile ca.pem dns.pem ip.pem`.

# PDF fixtures (`pdf/`)

Small PDFs for the PDF engine tests (`tests/test_pdf.py`) and the side-by-side
report (`docs/pdf_engine_report_0.2.0.md`). They were generated once with a
PDF writer outside this project and are public test material.

| File | Content |
| --- | --- |
| `basic.pdf` | Three pages: "alpha evidence", blank, "omega evidence". |
| `multicolumn.pdf` | One page, two columns of three lines each. |
| `nonlatin.pdf` | Chinese, Greek and Cyrillic lines, fonts embedded with Unicode maps. |
| `blank.pdf` | One empty page. |
| `scanned.pdf` | One page holding only an image (no text layer). |
| `encrypted.pdf` | AES-256 with a user password. |
| `fifty_pages.pdf` | Fifty pages, "page N evidence" on each. |
| `malformed.pdf` | A PDF header followed by a truncated fragment. |

These files are excluded from the wheel and the release ZIP.
