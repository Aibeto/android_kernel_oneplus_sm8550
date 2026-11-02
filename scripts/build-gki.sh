#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 构建 SM8550（OnePlus 11）stock GKI Image。
# 本地与 GitHub Actions 共用同一脚本，保证两边流程一致。
#
# 环境变量：
#   KDIR          common 源码树（默认 <repo>/common）
#   OUT           输出目录（默认 <repo>/out）
#   CLANG_DIR     clang bin 目录（默认 <repo>/.toolchain/clang-r450784e/bin，缺失时自动拉取）
#   JOBS          编译并行度（默认 nproc）
#   THINLTO_JOBS  ThinLTO 后端并行度（默认等于 JOBS）
#   LTO           thin | none（默认 thin）
#   CONFIG_ONLY   1 = 只生成 .config 后退出
#   KBUILD_BUILD_USER / KBUILD_BUILD_HOST  版本串中的构建者信息
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
KDIR=${KDIR:-$REPO_ROOT/common}
OUT=${OUT:-$REPO_ROOT/out}
JOBS=${JOBS:-$(nproc)}
THINLTO_JOBS=${THINLTO_JOBS:-$JOBS}
LTO=${LTO:-thin}

if [ -z "${CLANG_DIR:-}" ]; then
  CLANG_DIR="$REPO_ROOT/.toolchain/clang-r450784e/bin"
  if [ ! -x "$CLANG_DIR/clang" ]; then
    echo "== 本机没有工具链，先拉取到 $REPO_ROOT/.toolchain"
    "$REPO_ROOT/scripts/fetch-clang.sh"
  fi
fi
CLANG_DIR=$(cd "$CLANG_DIR" && pwd)
KDIR=$(cd "$KDIR" && pwd)

"$REPO_ROOT/scripts/setup-tree.sh"

for f in "$CLANG_DIR/clang" "$CLANG_DIR/ld.lld"; do
  [ -x "$f" ] || { echo "缺少 $f" >&2; exit 1; }
done
command -v pahole >/dev/null || { echo "缺少 pahole" >&2; exit 1; }

export PATH="$CLANG_DIR:$PATH"
export KBUILD_BUILD_USER=${KBUILD_BUILD_USER:-builder}
export KBUILD_BUILD_HOST=${KBUILD_BUILD_HOST:-buildhost}

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# 所有临时文件也放在 out/ 里，构建过程不写到工作区外
mkdir -p "$OUT/tmp"
export TMPDIR="$OUT/tmp"
LD_WRAPPER="$OUT/ld-thin.sh"
cat > "$LD_WRAPPER" <<EOF
#!/bin/bash
exec "$CLANG_DIR/ld.lld" "\$@" --thinlto-jobs=$THINLTO_JOBS
EOF
chmod +x "$LD_WRAPPER"

MAKE_ARGS=(
  ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu-
  CC="$CLANG_DIR/clang" HOSTCC="$CLANG_DIR/clang" HOSTLD=ld.lld
  PAHOLE="$(command -v pahole)" LD="$LD_WRAPPER"
)

cd "$KDIR"
echo "== clang: $("$CLANG_DIR/clang" --version | head -1)"
echo "== lld:   $("$CLANG_DIR/ld.lld" --version | head -1)"
echo "== kdir:  $KDIR"
echo "== out:   $OUT"
echo "== jobs:  $JOBS (thinlto $THINLTO_JOBS), lto=$LTO"

make O="$OUT" "${MAKE_ARGS[@]}" gki_defconfig
if [ "$LTO" = "thin" ]; then
  ./scripts/config --file "$OUT/.config" -e LTO_CLANG -e LTO_CLANG_THIN -d LTO_CLANG_FULL -d LTO_CLANG_NONE
else
  ./scripts/config --file "$OUT/.config" -d LTO_CLANG -e LTO_NONE
fi
make O="$OUT" "${MAKE_ARGS[@]}" olddefconfig
if [ "${CONFIG_ONLY:-0}" = "1" ]; then
  echo "CONFIG_OK"
  exit 0
fi
make -j"$JOBS" O="$OUT" "${MAKE_ARGS[@]}" Image
echo "BUILD_OK"