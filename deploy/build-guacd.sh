#!/usr/bin/env bash
# Build and install guacd (Apache Guacamole server) 1.6.0 with RDP support on Debian 13.
#
# Debian 13 (trixie) has no guacd package and ships FreeRDP 3.  guacamole-server 1.6.0
# supports FreeRDP 3, but its configure checks and sources are compiled with -Werror,
# and newer FreeRDP 3 headers mark several APIs as deprecated.  Building with the
# flags below turns those deprecation warnings back into warnings (tested with
# FreeRDP 3.x: RDP + vmconnect support is enabled).
set -euo pipefail

GUAC_VERSION="${GUAC_VERSION:-1.6.0}"
GUAC_SHA256="${GUAC_SHA256:-8bc45675da96d7b6f39728160181e3d4ff3c08f460f6d26de5805b642bf13f2b}"
PREFIX="${GUACD_PREFIX:-/usr/local}"
WORKDIR="$(mktemp -d /tmp/guacd-build.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

log() { echo -e "\e[1;34m[guacd]\e[0m $*"; }

log "Installing build dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool libtool-bin pkg-config curl ca-certificates \
    libcairo2-dev libjpeg62-turbo-dev libpng-dev uuid-dev libssl-dev libwebp-dev freerdp3-dev

TARBALL="guacamole-server-${GUAC_VERSION}.tar.gz"
cd "$WORKDIR"
for url in "https://downloads.apache.org/guacamole/${GUAC_VERSION}/source/${TARBALL}" \
           "https://archive.apache.org/dist/guacamole/${GUAC_VERSION}/source/${TARBALL}"; do
    log "Downloading $url"
    if curl -fsSLo "$TARBALL" "$url"; then break; fi
done
[ -s "$TARBALL" ] || { echo "Download of $TARBALL failed" >&2; exit 1; }
echo "${GUAC_SHA256}  ${TARBALL}" | sha256sum -c -

tar -xzf "$TARBALL"
cd "guacamole-server-${GUAC_VERSION}"
log "Configuring"
./configure --prefix="$PREFIX" --with-rdp --without-ssh --without-telnet --without-vnc --without-kubernetes \
    --disable-guacenc --disable-guaclog \
    CPPFLAGS="-Wno-error=deprecated-declarations" CFLAGS="-O2 -Wno-error" | tee configure.out
if ! grep -Eq "^\s+RDP \.+ yes" configure.out; then
    echo "RDP support was NOT enabled (FreeRDP not found?). See configure output above." >&2
    exit 1
fi
log "Building"
make -j"$(nproc)"
log "Installing to $PREFIX"
make install
ldconfig

id guacd >/dev/null 2>&1 || useradd --system --home-dir /var/lib/guacd --create-home --shell /usr/sbin/nologin guacd
log "guacd $("$PREFIX/sbin/guacd" -v 2>&1 | head -1) installed"
