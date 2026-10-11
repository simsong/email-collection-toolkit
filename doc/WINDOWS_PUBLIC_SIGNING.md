<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Microsoft public signing for Windows

Release and `[release-ci]` candidate calls select `public_signing: true` in
`windows-msix.yml`. Explicit dispatch defaults to local test signing; select public
signing to exercise production. Missing configuration fails the run; there is no
fallback to the test certificate. This implementation still needs Azure identity
approval, a certificate profile, federation, GitHub configuration and hosted CI.

The payload publisher is the exact approved certificate subject in the repository
variable `MSIX_PUBLISHER`. Microsoft retains the private key. The protected
`windows-signing` environment gates the bundle job; only that job requests an OIDC
token. Azure login exchanges the short-lived GitHub token, and Microsoft's pinned
Artifact Signing action signs the base and CI-only upgrade bundles with SHA-256 and
an RFC 3161 timestamp. Windows checks platform trust, timestamps and bundle identity
before upload. Public installation acceptance imports no certificate.

Release staging checks the bundle and Windows verification evidence against the
downloaded hash inventory. This establishes artifact consistency; Windows SignTool
and installation jobs establish trust. Release ZIPs omit test certificates and trust
helpers. Published assets remain immutable. Changing publisher creates a different
Windows package family: remove an old test installation before installing the public
build, while preserving archive directories.

## Provisioning

1. Complete individual identity validation in Azure Artifact Signing and create a
   **Public Trust** certificate profile. Copy its full subject into `MSIX_PUBLISHER`;
   do not derive a `CN` string from the display name.
2. Create a single-tenant Entra application and service principal without a client
   secret. Add one federated credential: issuer
   `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`,
   subject `repo:simsong/email-collection-toolkit:environment:windows-signing`.
3. Grant that service principal **Artifact Signing Certificate Profile Signer** on
   the selected profile only. It needs no Owner, Contributor or Identity Verifier
   role. The human verifier needs **Artifact Signing Identity Verifier** on the account;
   inherited Owner alone does not provide validation access.
4. Create `windows-signing` with required reviewer `simsong`, administrator bypass
   disabled, and restricted deployment branches/tags. Permit `main`, version tags
   `v*`, and explicitly approved candidate branches only. Review a candidate's full
   workflow/code before approving signing: the environment subject alone does not
   restrict which workflow can request signing. Allow self-review when necessary
   for the sole human administrator. Protect main/release publication separately.
5. Set the repository variables below, publish the change through review, then run
   public dispatch or candidate CI. Local tests cannot establish Azure access or
   Windows trust. Federation and security-sensitive role assignments require approval.

The created account is `simson-garfinkel-signing`, resource group `rg-simson-signing`,
East US, endpoint `https://eus.codesigning.azure.net/`. Tenant/subscription IDs are
recorded in the private setup memo.

## Variables and commands

Use repository variables so the payload can read its publisher before entering the
signing environment. Do not shadow the publisher with a different environment value.
Identifiers and names below are configuration, not credentials.

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Entra application ID for restricted GitHub federation |
| `AZURE_TENANT_ID` | Tenant containing that application |
| `AZURE_SUBSCRIPTION_ID` | Subscription containing the signing account |
| `AZURE_SIGNING_ENDPOINT` | Region-specific signing endpoint |
| `AZURE_SIGNING_ACCOUNT` | Artifact Signing account name |
| `AZURE_SIGNING_PROFILE` | Approved Public Trust certificate profile name |
| `MSIX_PUBLISHER` | Exact certificate subject for manifest and verification |
| `PUBLIC_SIGNING` | Workflow-selected boolean controlling assembly/installation mode |
| `MSIX_TEST_CERT_PFX_BASE64` | Development test key, used only in test mode |

`make test-release-files` checks staging and its rejection cases. On Windows,
`make msix-bundle MSIX_PAYLOADS=... MSIX_ARGS=-PublicSigning` assembles unsigned
bundles for the Microsoft action. After signing, `make verify-public-msix
MSIX_PUBLISHER='exact approved subject'` checks output. Hosted acceptance exercises
install, native launch, upgrade, archive preservation and uninstall on shared bundles.

## Maintenance

OIDC removes client-secret renewal. Keep billing active, renew identity validation by
the date shown in Azure, and monitor profile status. Timestamping is required because
signing certificates are short-lived. Review action pins, federation scope, environment
protection and roles when repositories/workflows change. Signing does not guarantee
every Windows warning is suppressed.

References: [Microsoft action](https://github.com/Azure/artifact-signing-action),
[roles](https://learn.microsoft.com/en-us/azure/artifact-signing/tutorial-assign-roles),
[OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect),
[SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).
