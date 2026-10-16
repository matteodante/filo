#!/bin/sh
set -eu

UV_VERSION="0.11.31"
UV_RELEASE_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
UV_CHECKSUMS_SHA256="cae3a06391dd65895dc22246115fd998250fa43ab3aa8ffd0d6ab71ae301b4e1"
UV_LICENSE_URL="https://raw.githubusercontent.com/astral-sh/uv/b7fdec626cdafcfb0d0db54d39d3d5f114aefb5c/LICENSE-MIT"
UV_LICENSE_SHA256="860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c"
FILO_VERSION="v0.3.1"
SOURCE_URL="https://github.com/matteodante/filo/archive/refs/tags/${FILO_VERSION}.tar.gz"

case "$(uname -s)" in
    Darwin|Linux) ;;
    *)
        echo "Filo installation supports macOS, Linux, and Windows." >&2
        exit 1
        ;;
esac

if ! { : </dev/tty; } 2>/dev/null; then
    echo "Filo setup requires an interactive terminal." >&2
    exit 1
fi

for command in curl tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command not found: $command" >&2
        exit 1
    fi
done
if [ "$(uname -s)" = "Darwin" ] && ! command -v script >/dev/null 2>&1; then
    echo "Required command not found: script" >&2
    exit 1
fi

TEMP_DIRECTORY="$(mktemp -d 2>/dev/null || mktemp -d -t filo-install)"
trap 'rm -rf "$TEMP_DIRECTORY"' EXIT HUP INT TERM

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        SHA256_RESULT="$(sha256sum "$1")"
    elif command -v shasum >/dev/null 2>&1; then
        SHA256_RESULT="$(shasum -a 256 "$1")"
    else
        echo "Required command not found: sha256sum or shasum" >&2
        exit 1
    fi
    printf '%s\n' "${SHA256_RESULT%% *}"
}

if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
else
    echo "Installing uv ${UV_VERSION}..."

    UV_TARGET=""
    case "$(uname -s):$(uname -m)" in
        Darwin:arm64|Darwin:aarch64) UV_TARGET="aarch64-apple-darwin" ;;
        Darwin:x86_64) UV_TARGET="x86_64-apple-darwin" ;;
        Linux:aarch64|Linux:arm64) UV_ARCH="aarch64" ;;
        Linux:arm|Linux:armv6l) UV_TARGET="arm-unknown-linux-musleabihf" ;;
        Linux:armv7l|Linux:armv7) UV_ARCH="armv7" ;;
        Linux:i386|Linux:i486|Linux:i586|Linux:i686) UV_ARCH="i686" ;;
        Linux:ppc64le|Linux:powerpc64le) UV_TARGET="powerpc64le-unknown-linux-gnu" ;;
        Linux:riscv64) UV_ARCH="riscv64gc" ;;
        Linux:s390x) UV_TARGET="s390x-unknown-linux-gnu" ;;
        Linux:x86_64|Linux:amd64) UV_ARCH="x86_64" ;;
        *)
            echo "uv ${UV_VERSION} has no supported artifact for $(uname -s) $(uname -m)." >&2
            exit 1
            ;;
    esac
    if [ -z "${UV_TARGET:-}" ]; then
        case "$(ldd --version 2>&1 || true)" in
            *musl*) UV_LIBC="musl" ;;
            *) UV_LIBC="gnu" ;;
        esac
        UV_TARGET="${UV_ARCH}-unknown-linux-${UV_LIBC}"
    fi

    UV_ARCHIVE_NAME="uv-${UV_TARGET}.tar.gz"
    UV_CHECKSUMS="$TEMP_DIRECTORY/uv-sha256.sum"
    UV_ARCHIVE="$TEMP_DIRECTORY/$UV_ARCHIVE_NAME"
    UV_LICENSE="$TEMP_DIRECTORY/uv-LICENSE-MIT"
    curl --proto '=https' --tlsv1.2 -LsSf "$UV_RELEASE_URL/sha256.sum" -o "$UV_CHECKSUMS"
    if [ "$(sha256_file "$UV_CHECKSUMS")" != "$UV_CHECKSUMS_SHA256" ]; then
        echo "The downloaded uv checksum manifest failed SHA-256 verification." >&2
        exit 1
    fi

    UV_ARCHIVE_SHA256=""
    while read -r UV_CHECKSUM UV_CHECKSUM_NAME; do
        if [ "${UV_CHECKSUM_NAME#\*}" = "$UV_ARCHIVE_NAME" ]; then
            UV_ARCHIVE_SHA256="$UV_CHECKSUM"
            break
        fi
    done <"$UV_CHECKSUMS"
    if [ -z "$UV_ARCHIVE_SHA256" ]; then
        echo "The verified uv checksum manifest has no entry for $UV_ARCHIVE_NAME." >&2
        exit 1
    fi

    curl --proto '=https' --tlsv1.2 -LsSf "$UV_RELEASE_URL/$UV_ARCHIVE_NAME" -o "$UV_ARCHIVE"
    if [ "$(sha256_file "$UV_ARCHIVE")" != "$UV_ARCHIVE_SHA256" ]; then
        echo "The downloaded uv archive failed SHA-256 verification." >&2
        exit 1
    fi
    curl --proto '=https' --tlsv1.2 -LsSf "$UV_LICENSE_URL" -o "$UV_LICENSE"
    if [ "$(sha256_file "$UV_LICENSE")" != "$UV_LICENSE_SHA256" ]; then
        echo "The downloaded uv license failed SHA-256 verification." >&2
        exit 1
    fi

    UV_EXTRACT_DIRECTORY="$TEMP_DIRECTORY/uv"
    mkdir -p "$UV_EXTRACT_DIRECTORY" "$HOME/.local/bin"
    tar -xzf "$UV_ARCHIVE" -C "$UV_EXTRACT_DIRECTORY" --strip-components=1
    for UV_BINARY in uv uvx; do
        if [ ! -f "$UV_EXTRACT_DIRECTORY/$UV_BINARY" ]; then
            echo "The verified uv archive is incomplete." >&2
            exit 1
        fi
        cp "$UV_EXTRACT_DIRECTORY/$UV_BINARY" "$HOME/.local/bin/$UV_BINARY"
        chmod 0755 "$HOME/.local/bin/$UV_BINARY"
    done
    cp "$UV_LICENSE" "$HOME/.local/bin/uv-LICENSE-MIT"

    UV="$HOME/.local/bin/uv"
    if [ ! -x "$UV" ]; then
        echo "uv was installed but could not be found at $UV." >&2
        exit 1
    fi
fi

echo "Downloading Filo ${FILO_VERSION}..."
curl -LsSf "$SOURCE_URL" | tar -xz -C "$TEMP_DIRECTORY" --strip-components=1
if [ ! -f "$TEMP_DIRECTORY/pyproject.toml" ]; then
    echo "The downloaded Filo source is incomplete." >&2
    exit 1
fi

echo "Installing Filo..."
"$UV" tool install --force --python 3.12 "$TEMP_DIRECTORY"
"$UV" tool update-shell

TOOL_BIN="$("$UV" tool dir --bin)"
FILO="$TOOL_BIN/filo"
if [ ! -x "$FILO" ]; then
    echo "Filo was installed but $FILO is unavailable." >&2
    exit 1
fi

case "$(uname -s)" in
    Darwin) script -q -e /dev/null "$FILO" setup </dev/tty ;;
    Linux) "$FILO" setup </dev/tty ;;
esac
"$FILO" doctor

echo
echo "Filo is ready. Start it with: filo chat"
echo "If filo is not found in a new command, restart your shell."
