#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 用仓库内的 AnyKernel3 模板（third_party/AnyKernel3）打可刷入的 zip。
# 模板内容与刷机脚本分离：anykernel.sh 以 scripts/anykernel.sh 为准，
# 只把 Image 和它拼进临时目录，不改动子模块工作区。
#
# 环境变量：
#   IMAGE        要打包的 Image（默认 <repo>/out/arch/arm64/boot/Image）
#   OUT_DIR      产物目录（默认 <repo>/artifacts）
#   AK3_DIR      模板目录（默认 <repo>/third_party/AnyKernel3）
#   STAGE        打包中转目录（默认 <repo>/out/ak3-stage，可重复使用）
#   NAME         zip 名前缀（默认 AnyKernel3_stock_sm8550）
#   TAG          版本后缀（默认 common 子模块的短 commit）
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
AK3_DIR=${AK3_DIR:-$REPO_ROOT/third_party/AnyKernel3}
IMAGE=${IMAGE:-$REPO_ROOT/out/arch/arm64/boot/Image}
OUT_DIR=${OUT_DIR:-$REPO_ROOT/artifacts}
STAGE=${STAGE:-$REPO_ROOT/out/ak3-stage}
NAME=${NAME:-AnyKernel3_stock_sm8550}

[ -f "$IMAGE" ] || { echo "缺少内核镜像: $IMAGE" >&2; exit 1; }
[ -f "$AK3_DIR/tools/ak3-core.sh" ] || {
  echo "缺少 AnyKernel3 模板: $AK3_DIR" >&2
  echo "先执行: git submodule update --init --depth 1 third_party/AnyKernel3" >&2
  exit 1
}

if [ -z "${TAG:-}" ]; then
  TAG=$(git -C "$REPO_ROOT/common" rev-parse --short=8 HEAD 2>/dev/null || true)
fi
if [ -z "${TAG:-}" ]; then
  TAG=$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || echo unknown)
fi

mkdir -p "$STAGE" "$OUT_DIR"
OUT_DIR=$(cd "$OUT_DIR" && pwd)
STAGE=$(cd "$STAGE" && pwd)
cp -a "$AK3_DIR"/. "$STAGE"/
cp -f "$REPO_ROOT/scripts/anykernel.sh" "$STAGE/anykernel.sh"
IMAGE=$(cd "$(dirname "$IMAGE")" && pwd)/$(basename "$IMAGE")
cp -f "$IMAGE" "$STAGE/Image"

ZIP="$OUT_DIR/${NAME}_${TAG}.zip"
( cd "$STAGE" && zip -r9 "$ZIP" . -x ".git*" >/dev/null )

echo "== 产物: $ZIP"
ls -lh "$ZIP"
( cd "$OUT_DIR" && sha256sum "$(basename "$ZIP")" )