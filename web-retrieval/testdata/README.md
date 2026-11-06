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
