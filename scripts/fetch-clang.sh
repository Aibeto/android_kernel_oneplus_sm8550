#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 拉取 kernel_manifest(oneplus/sm8550) 固定的 clang-r450784e，
# 安装到工作区内部的 .toolchain/（不写到工作区外）。
#
# 环境变量：
#   CLANG_ROOT  工具链根目录（默认 <repo>/.toolchain）
#   CLANG_REV   CodeLinaro 上的 commit（默认 manifest 固定值）
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CLANG_ROOT=${CLANG_ROOT:-$REPO_ROOT/.toolchain}
CLANG_REV=${CLANG_REV:-c420e3ab93e51a2f2ab4f8e8b517463c34a7e8bd}

mkdir -p "$CLANG_ROOT"
CLANG_ROOT=$(cd "$CLANG_ROOT" && pwd)
DEST="$CLANG_ROOT/clang-r450784e"

if [ -x "$DEST/bin/clang" ]; then
  echo "== 已存在: $DEST"
  "$DEST/bin/clang" --version | head -1
  exit 0
fi

WORK="$CLANG_ROOT/work-$$"
mkdir -p "$WORK"
cd "$WORK"
git init -q
git remote add origin https://git.codelinaro.org/clo/la/kernelplatform/prebuilts-master/clang/host/linux-x86.git
git config core.sparseCheckout true
printf 'clang-r450784e/*\n' > .git/info/sparse-checkout
if ! git fetch --depth 1 --filter=blob:none origin "$CLANG_REV"; then
  echo "== blob:none 不受支持，回退普通浅取" >&2
  git fetch --depth 1 origin "$CLANG_REV"
fi
git checkout -q FETCH_HEAD
test -x clang-r450784e/bin/clang
mv clang-r450784e "$DEST"
cd "$CLANG_ROOT"
mkdir -p "$CLANG_ROOT/.trash"
mv "$WORK" "$CLANG_ROOT/.trash/work-$(date +%s)"
echo "== 已安装: $DEST"
"$DEST/bin/clang" --version | head -1
