# India PKI root certificates (CCA)

Downloaded on 2026-10-24 over HTTPS from the Controller of Certifying Authorities: https://cca.gov.in/root_certificate.html
Used as trust anchors for PDF digital-signature validation (`PDF_TRUST_ROOTS`).

Verify the fingerprints out of band before relying on them: CCA states that an email to verifyroot@cca.gov.in returns the root thumbprints.

| File | Subject | Valid from | Valid to | SHA-256 fingerprint (DER) |
|---|---|---|---|---|
| CCAIndia2022.pem | CN=CCA India 2022,O=India PKI,C=IN | 2022-02-02 | 2042-02-02 | `9a3fd3176798e842ddcb12c262f11cfacca70a8b84c6ea6fda30842a95a94cd8` |
| CCAIndia2022SPL.pem | CN=CCA India 2022 SPL,O=India PKI,C=IN | 2022-09-20 | 2042-09-20 | `b724689b79b2ef9421ef8f5cc733eb093851b170ee715177005a09f226d8c91a` |
| CCAIndia2015.pem | CN=CCA India 2015 SPL,O=India PKI,C=IN | 2015-01-29 | 2025-01-29 | `c34c5df53080078ffe45b21a7f600469917204f4f0293f1d7209393e5265c04f` |
| CCAIndia2014.pem | CN=CCA India 2014,O=India PKI,C=IN | 2014-03-05 | 2024-03-05 | `60109bc6c38328598a112c7a25e38b0f23e5a7511cb815fb64e0c4ff05db7df7` |

Expired roots are kept so that signatures made while they were valid can still be chain-checked.
