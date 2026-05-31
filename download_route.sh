#!/bin/bash
# 下载 DJI 云端航线 KMZ 文件
# 用法: ./download_route.sh <wayline_id> [output_path]
# 示例: ./download_route.sh 908b0ea4-2c50-4cca-9dbb-8555462a18d2 /tmp/route.kmz

set -e

WAYLINE_ID="${1?用法: $0 <wayline_id> [output_path]}"
OUTPUT="${2:-/tmp/${WAYLINE_ID}.kmz}"

BASE_URL="https://drone-java-api.metop.com.cn"
WORKSPACE_ID="fc4c751a-42ad-11f1-93b9-6c92bf9e870c"
USERNAME="adminPC1"
PASSWORD="adminPC1234567890"

# 1. 登录获取 token
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/v1/manage/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\",\"flag\":1}")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('access_token',''))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "登录失败，请检查用户名密码" >&2
  exit 1
fi

# 2. 下载 KMZ 文件（DJI 返回 302 重定向到 MinIO 预签名 URL，-L 自动跟随）
curl -s -L -X GET "${BASE_URL}/api/v1/wayline/workspaces/${WORKSPACE_ID}/waylines/${WAYLINE_ID}/url" \
  -H "x-auth-token: ${TOKEN}" \
  -o "${OUTPUT}"

# 3. 校验
if [ -s "${OUTPUT}" ]; then
  # 检查是否是有效 ZIP（KMZ 即为 ZIP）
  if python3 -c "import zipfile; zipfile.ZipFile('${OUTPUT}').close()" 2>/dev/null; then
    echo "下载成功: ${OUTPUT} ($(wc -c < "${OUTPUT}") 字节)"
  else
    echo "下载文件不是有效的 KMZ/ZIP 格式" >&2
    exit 1
  fi
else
  echo "下载失败，文件为空" >&2
  exit 1
fi