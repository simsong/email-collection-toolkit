Email Collection Toolkit for Windows x64

Open base.msixbundle with Windows App Installer. The bundle has a public-trust
signature and timestamp from Microsoft Artifact Signing. No test certificate
or certificate-import script is needed. WebView2 is an external prerequisite.

sha256.json records the artifact hashes. signing-verification.json records the
Windows CI signature gate; it is evidence, not an independent signing authority.

The approved publisher changes the package family from earlier test-signed
builds. Remove an old test installation before installing this publisher's
package. Preserve your email archives; do not delete archive directories.
