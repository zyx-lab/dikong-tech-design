# 上游航线 `curl` 复现

目标：只用 `curl` 直连 upstream 和对象存储，不经过 Django `/api/v1/routes/*`。

已验证环境：

- upstream base URL: `http://8.129.135.140`
- upstream 账号: `adminPC`
- upstream 密码: `adminPC1234567890`
- 测试文件: `routes/test.kmz`

## 1. 登录 upstream

```bash
BASE_URL="http://8.129.135.140"
USERNAME="adminPC"
PASSWORD="adminPC1234567890"

LOGIN_JSON=$(curl -sS -X POST "$BASE_URL/api/v1/manage/login" \
  -H 'Content-Type: application/json' \
  --data "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\",\"flag\":1}")

printf '%s\n' "$LOGIN_JSON"
```

提取 token：

```bash
TOKEN=$(printf '%s' "$LOGIN_JSON" | python3 -c 'import sys,json; print((json.load(sys.stdin).get("data") or {}).get("access_token",""))')

printf 'token_len=%s\n' "${#TOKEN}"
```

## 2. 读取 workspace

```bash
WORKSPACE_JSON=$(curl -sS \
  -H "x-auth-token: $TOKEN" \
  "$BASE_URL/api/v1/manage/workspaces/current")

printf '%s\n' "$WORKSPACE_JSON"
```

提取 `workspace_id`：

```bash
WORKSPACE_ID=$(printf '%s' "$WORKSPACE_JSON" | python3 -c 'import sys,json; print((json.load(sys.stdin).get("data") or {}).get("workspace_id",""))')

printf 'workspace_id=%s\n' "$WORKSPACE_ID"
```

## 3. 直接上传 `routes/test.kmz`

```bash
KMZ_PATH="$(pwd)/routes/test.kmz"

UPLOAD_JSON=$(curl -sS -X POST \
  "$BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/files/upload" \
  -H "x-auth-token: $TOKEN" \
  -F "name=direct-upstream-repro" \
  -F "file=@$KMZ_PATH;type=application/octet-stream")

printf '%s\n' "$UPLOAD_JSON"
```

提取 `wayline_id`：

```bash
WAYLINE_ID=$(printf '%s' "$UPLOAD_JSON" | python3 -c 'import sys,json; data=json.load(sys.stdin).get("data") or {}; print(data.get("wayline_id") or data.get("dji_wayline_id") or "")')

printf 'wayline_id=%s\n' "$WAYLINE_ID"
```

## 4. 让 upstream 解析出真实下载地址

这一步返回的是 `302 Location`，不是 JSON。

```bash
curl -sS -D /tmp/wayline-url.headers -o /tmp/wayline-url.body \
  -H "x-auth-token: $TOKEN" \
  "$BASE_URL/api/v1/wayline/workspaces/$WORKSPACE_ID/waylines/$WAYLINE_ID/url"

sed -n '1,20p' /tmp/wayline-url.headers
```

提取 `Location`：

```bash
RESOLVED_URL=$(grep -i '^Location:' /tmp/wayline-url.headers | sed 's/^Location: //I' | tr -d '\r')

printf 'resolved_url=%s\n' "$RESOLVED_URL"
```

## 5. 直接请求对象存储

```bash
curl -sS -I "$RESOLVED_URL"
```

```bash
curl -sS -i "$RESOLVED_URL" | sed -n '1,30p'
```

## 6. 怎么判断

如果第 5 步返回：

- `200 OK`
  说明这次 upstream 上传和对象存储链路是通的。
- `404 Not Found` 且响应体里有 `NoSuchKey`
  说明 upstream 已经给出了 presigned URL，但对象存储里没有对应对象。
  这时问题在 upstream 上传/对象存储链路，不在 Django `/api/v1/routes/{id}/kmz`。

典型坏样本会长这样：

```xml
<Error>
  <Code>NoSuchKey</Code>
  <Message>The specified key does not exist.</Message>
  <Key>wayline/test.kmz</Key>
</Error>
```

## 7. 当前备注

`2026-04-13` 实测：

- 用上面命令直接上传 `routes/test.kmz`，对象下载返回 `200`
- 说明 `NoSuchKey` 不是每次 fresh upload 都稳定复现
- 如果线上再次出现坏样本，优先保留当时的 `Location` 地址，然后直接执行第 5 步
