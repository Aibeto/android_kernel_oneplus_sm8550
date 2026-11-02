#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 把内核期望的 vendor / oplus 路径映射到工作区内部的 devicetree 子模块，
# 避免在仓库外创建任何软链接。幂等，可重复执行。
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMMON=${COMMON:-$REPO_ROOT/common}
DT=${DT:-$REPO_ROOT/devicetree/sm8550}

[ -d "$DT/vendor" ] || { echo "缺少 $DT/vendor" >&2; exit 1; }
[ -d "$DT/kernel_platform/oplus" ] || { echo "缺少 $DT/kernel_platform/oplus" >&2; exit 1; }

link() {
  local path=$1
  local target=$2
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then
    return 0
  fi
  ln -sfn "$target" "$path"
  echo "link: ${path#"$REPO_ROOT"/} -> $target"
}

link "$REPO_ROOT/vendor" "devicetree/sm8550/vendor"
link "$REPO_ROOT/oplus" "devicetree/sm8550/kernel_platform/oplus"
link "$COMMON/kernel/oplus_cpu" "../../vendor/oplus/kernel/cpu"
link "$COMMON/drivers/soc/oplus/storage" "../../../../vendor/oplus/kernel/storage"

if git -C "$COMMON" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$COMMON" update-index --skip-worktree kernel/oplus_cpu drivers/soc/oplus/storage 2>/dev/null || true
fi

echo "== 路径映射就绪（vendor/oplus 均在工作区内）"
