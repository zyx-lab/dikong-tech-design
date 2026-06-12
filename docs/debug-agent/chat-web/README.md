# Streamlit Chat Web UI

This directory runs a LAN-only Streamlit chatbot for the Dikong debug agent.

Streamlit provides the browser UI. Hermes Agent remains the model/tool runtime.
The page supports creating, renaming, switching, and deleting local chat sessions.

## Topology

```text
LAN browser
  -> http://<server-lan-ip>:7860
  -> Streamlit chat-web
  -> http://127.0.0.1:8642/v1/chat/completions
  -> Hermes Agent API server
  -> Dikong debug skills
```

Only the Streamlit chat page is bound to the LAN. Hermes API server stays on
`127.0.0.1`.

## Dependencies

The local virtual environment lives at:

```text
docs/debug-agent/chat-web/.venv
```

Install or refresh dependencies:

```bash
/home/charles/.hermes/bin/uv venv --python 3.12 docs/debug-agent/chat-web/.venv
/home/charles/.hermes/bin/uv pip install \
  --python docs/debug-agent/chat-web/.venv/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  -r docs/debug-agent/chat-web/requirements.txt
```

## Required Hermes API Server Settings

The Streamlit app reads `/home/charles/.hermes/.env`.

Required values:

```text
API_SERVER_ENABLED=true
API_SERVER_KEY=<secret>
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
```

Optional values:

```text
CHAT_WEB_HOST=0.0.0.0
CHAT_WEB_PORT=7860
CHAT_WEB_SESSIONS_PATH=/home/charles/.hermes/dikong-debug-chat/sessions.json
```

Do not bind Hermes API server itself to the LAN. It exposes API access backed by
the local model credentials.

## Start

Start Hermes API server through the Hermes gateway:

```bash
nohup hermes gateway run --accept-hooks \
  > /home/charles/.hermes/logs/gateway.log 2>&1 &
echo $! > /home/charles/.hermes/gateway.pid
```

Verify:

```bash
curl -fsS http://127.0.0.1:8642/health
curl -fsS http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer $(grep '^API_SERVER_KEY=' /home/charles/.hermes/.env | cut -d= -f2-)"
```

Start the Streamlit chat page:

```bash
nohup docs/debug-agent/chat-web/.venv/bin/streamlit run docs/debug-agent/chat-web/server.py \
  --server.address "${CHAT_WEB_HOST:-0.0.0.0}" \
  --server.port "${CHAT_WEB_PORT:-7860}" \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --browser.gatherUsageStats false \
  > /home/charles/.hermes/logs/dikong-debug-chat.log 2>&1 &
echo $! > /home/charles/.hermes/dikong-debug-chat.pid
```

Open from the LAN:

```text
http://<server-lan-ip>:7860
```

On this machine, get the LAN IP with:

```bash
hostname -I | awk '{print $1}'
```

## Stop

```bash
kill "$(cat /home/charles/.hermes/dikong-debug-chat.pid)"
kill "$(cat /home/charles/.hermes/gateway.pid)"
```

## Sessions

Chat sessions are stored locally as JSON:

```text
/home/charles/.hermes/dikong-debug-chat/sessions.json
```

The Web UI can create, rename, switch, and delete sessions. Deployment
credentials can be supplied in chat when they are needed for one-use local
authenticated debug requests.

## Test

```bash
docs/debug-agent/chat-web/.venv/bin/python -m unittest -v docs/debug-agent/chat-web/test_server.py
```
