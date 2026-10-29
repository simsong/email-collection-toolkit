#!/bin/bash
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Build the macOS CI antivirus runtime without Homebrew's moving ClamAV bottle.
# Authenticate pinned upstream source archives before extracting or compiling.
# Build OpenSSL first, then select it explicitly for ClamAV's C and Rust code.
# Install only beneath this checkout, preserving licenses and CVD certificates.
# Verify native linkage before exporting the application's existing overrides.
# CI, candidate validation and release packaging share this Makefile entry point.
set -euo pipefail

[[ "$(uname -s)" == Darwin ]] || { echo 'This target requires macOS.' >&2; exit 1; }
root="$(cd "$(dirname "$0")/.." && pwd)/.tmp/ci-clamav"
openssl_version=3.6.4
clamav_version=1.5.4
openssl="$root/openssl-$openssl_version"
clamav="$root/clamav-$clamav_version"
mkdir -p "$root/downloads"
work=$(mktemp -d "$root/build.XXXXXX")
trap 'rm -rf "$work"' EXIT
cd "$work"
jobs=$(sysctl -n hw.logicalcpu)
if (( jobs > 8 )); then jobs=8; fi

download() {
    local url=$1 digest=$2 archive="$root/downloads/$3"
    if [[ ! -f "$archive" ]]; then
        curl --fail --location --retry 3 --output "$archive.partial" "$url"
        mv "$archive.partial" "$archive"
    fi
    printf '%s  %s\n' "$digest" "$archive" | shasum -a 256 -c -
    tar -xzf "$archive" -C "$work"
}

download "https://github.com/openssl/openssl/releases/download/openssl-$openssl_version/openssl-$openssl_version.tar.gz" \
    9bffaa1ad1e07b354c21bd3324ec02fa15579f45a7d0494b3e74bc449b7333ef "openssl-$openssl_version.tar.gz"
download "https://github.com/Cisco-Talos/clamav/releases/download/clamav-$clamav_version/clamav-$clamav_version.tar.gz" \
    1af1117a228f1b5bc7fa91a0dabc37848a99e7d25188e9be8043332ce721dfd3 "clamav-$clamav_version.tar.gz"

(
    cd "$work/openssl-$openssl_version"
    ./Configure --prefix="$openssl" --openssldir="$openssl/ssl" --libdir=lib shared no-tests
    make -j"$jobs"
    make install_sw
    install -m 644 LICENSE.txt "$openssl/LICENSE.txt"
)

export OPENSSL_DIR="$openssl" OPENSSL_LIB_DIR="$openssl/lib" OPENSSL_INCLUDE_DIR="$openssl/include"
export OPENSSL_NO_VENDOR=1 CARGO_BUILD_JOBS="$jobs"
export PKG_CONFIG_PATH="$openssl/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
cmake -S "$work/clamav-$clamav_version" -B "$work/clamav-build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$clamav" \
    -DOPENSSL_ROOT_DIR="$openssl" -DOPENSSL_INCLUDE_DIR="$openssl/include" \
    -DOPENSSL_CRYPTO_LIBRARY="$openssl/lib/libcrypto.3.dylib" \
    -DOPENSSL_SSL_LIBRARY="$openssl/lib/libssl.3.dylib" \
    -DAPP_CONFIG_DIRECTORY="$clamav/etc/clamav" -DCVD_CERTS_DIRECTORY="$clamav/etc/clamav/certs" \
    -DENABLE_JSON_SHARED=ON -DENABLE_STATIC_LIB=OFF -DENABLE_TESTS=OFF \
    -DENABLE_MILTER=OFF -DENABLE_MAN_PAGES=OFF -DENABLE_EXAMPLES=OFF
cmake --build "$work/clamav-build" --parallel "$jobs"
cmake --install "$work/clamav-build"
install -m 644 "$work/clamav-$clamav_version/COPYING.txt" "$clamav/COPYING.txt"

[[ "$("$openssl/bin/openssl" version)" == "OpenSSL $openssl_version "* ]]
for library in "$clamav/lib/libclamav.dylib" "$clamav/lib/libfreshclam.dylib"; do
    dependencies=$(/usr/bin/otool -L "$library")
    printf '%s\n' "$dependencies"
    [[ "$dependencies" == *"$openssl/lib/libcrypto.3.dylib"* ]]
    [[ "$dependencies" != *libcrypto.4.dylib* && "$dependencies" != *libssl.4.dylib* ]]
done
test -s "$clamav/etc/clamav/certs/clamav.crt"
printf 'DatabaseMirror database.clamav.net\nCVDCertsDirectory %s\nTestDatabases yes\n' \
    "$clamav/etc/clamav/certs" > "$work/freshclam.conf"
"$clamav/bin/freshclam" --version --config-file="$work/freshclam.conf"
{
    printf 'export MAILARCHIVER_CLAMAV_LIBRARY=%q\n' "$clamav/lib/libclamav.dylib"
    printf 'export MAILARCHIVER_FRESHCLAM=%q\n' "$clamav/bin/freshclam"
    printf 'export MAILARCHIVER_CLAMAV_CERTIFICATES=%q\n' "$clamav/etc/clamav/certs"
} > "$root/environment.sh"
if [[ -n "${GITHUB_ENV:-}" ]]; then
    printf 'MAILARCHIVER_CLAMAV_LIBRARY=%s\nMAILARCHIVER_FRESHCLAM=%s\nMAILARCHIVER_CLAMAV_CERTIFICATES=%s\n' \
        "$clamav/lib/libclamav.dylib" "$clamav/bin/freshclam" "$clamav/etc/clamav/certs" >> "$GITHUB_ENV"
fi
