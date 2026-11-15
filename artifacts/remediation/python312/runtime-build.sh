#!/bin/bash
set -euo pipefail
ROOT=/home/user/EnergyBaseline
WORK="$ROOT/.cache/migration312"
PREFIX="$WORK/prefix"
mkdir -p "$WORK/src" "$PREFIX/include" "$PREFIX/lib"
cd "$WORK/src"
fetch() {
  local name="$1" url="$2"
  if [ ! -d "$name" ]; then
    curl --fail --location --max-time 120 "$url" -o "$WORK/$name.tar.gz"
    mkdir -p "$name"
    tar xzf "$WORK/$name.tar.gz" -C "$name" --strip-components=1
  fi
}
fetch openssl https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-3.0.18
(cd openssl; perl Configure --prefix="$PREFIX" --libdir=lib no-tests no-shared -fPIC; make -j2; make install_sw)
fetch zlib https://codeload.github.com/madler/zlib/tar.gz/refs/tags/v1.3.1
(cd zlib; ./configure --prefix="$PREFIX"; make -j2; make install)
fetch libffi https://codeload.github.com/libffi/libffi/tar.gz/refs/tags/v3.4.6
# Generate the public x86-64 ABI header against the sandbox's installed libffi 8.
cp libffi/src/x86/ffitarget.h "$PREFIX/include/ffitarget.h"
sed -e 's/@TARGET@/X86_64/g' -e 's/@HAVE_LONG_DOUBLE@/1/g' -e 's/@VERSION@/3.4.6/g' -e 's/@FFI_EXEC_TRAMPOLINE_TABLE@/0/g' libffi/include/ffi.h.in > "$PREFIX/include/ffi.h"
ln -sf /usr/lib/x86_64-linux-gnu/libffi.so.8 "$PREFIX/lib/libffi.so"
cp "$ROOT/.cache/adapter-runtime/prefix/include/sqlite3.h" "$PREFIX/include/"
ln -sf /usr/lib/x86_64-linux-gnu/libsqlite3.so.0 "$PREFIX/lib/libsqlite3.so"
fetch bzip2 https://codeload.github.com/libarchive/bzip2/tar.gz/refs/tags/bzip2-1.0.8
cp bzip2/bzlib.h "$PREFIX/include/"
ln -sf /usr/lib/x86_64-linux-gnu/libbz2.so.1 "$PREFIX/lib/libbz2.so"
fetch xz https://codeload.github.com/tukaani-project/xz/tar.gz/refs/tags/v5.4.1
cp -r xz/src/liblzma/api/lzma "$PREFIX/include/"
cp xz/src/liblzma/api/lzma.h "$PREFIX/include/"
ln -sf /usr/lib/x86_64-linux-gnu/liblzma.so.5 "$PREFIX/lib/liblzma.so"
fetch cpython https://codeload.github.com/python/cpython/tar.gz/refs/tags/v3.12.16
cd cpython
CPPFLAGS="-I$PREFIX/include" LDFLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib" LIBSQLITE3_CFLAGS="-I$PREFIX/include" LIBSQLITE3_LIBS="-L$PREFIX/lib -lsqlite3" LIBFFI_CFLAGS="-I$PREFIX/include" LIBFFI_LIBS="-L$PREFIX/lib -lffi" ./configure --prefix="$WORK/python" --with-openssl="$PREFIX" --with-ensurepip=install
make -j2
make install
"$WORK/python/bin/python3.12" -c 'import ssl,sqlite3,ctypes,zlib,bz2,lzma; print(ssl.OPENSSL_VERSION); print(sqlite3.sqlite_version)'
"$WORK/python/bin/python3.12" -m venv "$WORK/venv"
