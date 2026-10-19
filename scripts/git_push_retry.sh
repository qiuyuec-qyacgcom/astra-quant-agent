#!/usr/bin/env bash
# git 推送包装脚本 —— 应对 GitHub 直连链路丢包导致的间歇性失败
#
# 背景（2026-10 实测）：
#   1. TLS 1.3 在该链路被完全阻断，OpenSSL 默认先试 1.3 会卡到超时；
#      强制 tlsv1.2 后能连通。
#   2. 即使锁定 TLS 1.2，git 传输层仍间歇性卡死（TCP 连接 0.2s 成功、
#      TLS 握手 0.5s 成功，但 HTTP 响应传输阶段 40s+ 无数据）。
#   3. 失败是概率性的，同一命令重试往往几次内就能成功。
#
# 用法：
#   ./scripts/git_push_retry.sh              # 推送到 origin main
#   ./scripts/git_push_retry.sh origin main  # 指定 remote / branch
#
# 前置条件（已写入全局配置，此处再兜一层，防止换机器后失效）：
#   git config --global http.sslVersion tlsv1.2
#   git config --global http.version HTTP/1.1
#   git config --global http.lowSpeedLimit 100
#   git config --global http.lowSpeedTime 20

set -uo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-main}"
MAX_TRIES="${GIT_PUSH_TRIES:-8}"
SINGLE_TIMEOUT="${GIT_PUSH_TIMEOUT:-70}"

cd "$(git rev-parse --show-toplevel)" || exit 1

echo "推送目标: $REMOTE $BRANCH"
echo "最多尝试 $MAX_TRIES 次，单次上限 ${SINGLE_TIMEOUT}s"
echo

for i in $(seq 1 "$MAX_TRIES"); do
  printf "尝试 %d/%d: " "$i" "$MAX_TRIES"

  OUT=$(timeout "$SINGLE_TIMEOUT" git \
    -c http.sslVersion=tlsv1.2 \
    -c http.version=HTTP/1.1 \
    -c http.lowSpeedLimit=100 \
    -c http.lowSpeedTime=20 \
    push "$REMOTE" "$BRANCH" 2>&1)
  RC=$?

  if [ $RC -eq 0 ]; then
    echo "成功"
    echo "$OUT" | sed 's/^/  /'
    echo
    echo "远端 HEAD: $(git rev-parse --short "$REMOTE/$BRANCH" 2>/dev/null || echo '?')"
    exit 0
  fi

  # 区分「网络层失败」与「业务层拒绝」
  # 业务层拒绝（非快进、权限不足）重试无意义，直接退出
  if echo "$OUT" | grep -qE 'rejected|non-fast-forward|Permission|protected branch|does not exist'; then
    echo "被拒绝（非网络问题，重试无效）"
    echo "$OUT" | sed 's/^/  /'
    exit 1
  fi

  echo "失败 (rc=$RC): $(echo "$OUT" | head -1 | cut -c1-100)"
  if [ "$i" -lt "$MAX_TRIES" ]; then
    sleep 4
  fi
done

echo
echo "全部 $MAX_TRIES 次均失败。"
echo "备选方案：用 GitHub Contents API 逐文件写入（见 scripts/push_via_api.py）。"
exit 1
