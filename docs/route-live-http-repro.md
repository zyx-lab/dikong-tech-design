# Route HTTP Repro

## 2. 准备前端联调账号


```bash
export BASE_URL=http://8.129.135.140:8010
export TENANT_CODE=frontend_lab
export USERNAME=fe_frontend_lab_route
export PASSWORD='FrontTest@123'
export KMZ_PATH=/home/charles/dikong-tech-design/routes/test.kmz
```

## 3. 登录

```bash
curl -sS -X POST "$BASE_URL/api/v1/iam/session/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" \
  > /tmp/route-login.json

cat /tmp/route-login.json

export ACCESS_TOKEN=$(
  python3 -c 'import json; print(json.load(open("/tmp/route-login.json"))["data"]["accessToken"])'
)
```

## 4. 创建航线

```bash
curl -sS -X POST "$BASE_URL/api/v1/routes" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -F 'name=Frontend联调航线' \
  -F "kmz_file=@$KMZ_PATH;type=application/vnd.google-earth.kmz" \
  > /tmp/route-create.json

cat /tmp/route-create.json

export ROUTE_ID=$(
  python3 -c 'import json; print(json.load(open("/tmp/route-create.json"))["data"]["id"])'
)
```

## 5. 列表和详情

```bash
curl -sS "$BASE_URL/api/v1/routes" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE"

curl -sS "$BASE_URL/api/v1/routes/$ROUTE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE"
```

## 6. 下载 KMZ

```bash
curl -sS "$BASE_URL/api/v1/routes/$ROUTE_ID/kmz" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -D /tmp/route-kmz.headers \
  -o /tmp/route-$ROUTE_ID.kmz

cat /tmp/route-kmz.headers
wc -c /tmp/route-$ROUTE_ID.kmz
```

## 7. 只更新名称（JSON）

```bash
curl -sS -X PUT "$BASE_URL/api/v1/routes/$ROUTE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Frontend联调航线-仅改名称"}'
```

## 8. 替换 KMZ（保留原名称）

```bash
curl -sS -X PUT "$BASE_URL/api/v1/routes/$ROUTE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -F "kmz_file=@$KMZ_PATH;type=application/vnd.google-earth.kmz"
```

## 9. 同时更新名称和 KMZ

```bash
curl -sS -X PUT "$BASE_URL/api/v1/routes/$ROUTE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -F 'name=Frontend联调航线-更新' \
  -F "kmz_file=@$KMZ_PATH;type=application/vnd.google-earth.kmz"
```

## 10. 删除航线

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/routes/$ROUTE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE"
```
