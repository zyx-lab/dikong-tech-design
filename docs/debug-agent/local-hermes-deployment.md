# Local Hermes Deployment

This machine runs Hermes Agent as the runtime and a Streamlit Web UI as the LAN
chatbot for frontend debugging.

## Runtime

```text
Hermes home: /home/charles/.hermes
Hermes source: /home/charles/.hermes/hermes-agent
Hermes command: /home/charles/.local/bin/hermes
Hermes version: 0.16.0
Streamlit app: docs/debug-agent/chat-web/server.py
```

Hermes model/provider settings live in:

```text
/home/charles/.hermes/config.yaml
```

Secrets live in:

```text
/home/charles/.hermes/.env
```

Do not commit or print that file.

## Project Skills

The four project debug skills are symlinked into Hermes:

```text
/home/charles/.hermes/skills/frontend-api-debug -> docs/debug-agent/skills/frontend-api-debug
/home/charles/.hermes/skills/schema-debug -> docs/debug-agent/skills/schema-debug
/home/charles/.hermes/skills/log-debug -> docs/debug-agent/skills/log-debug
/home/charles/.hermes/skills/regression-debug -> docs/debug-agent/skills/regression-debug
```

The custom `dikong_code_context` toolset gives Hermes bounded access to the
Dikong checkout, the DJI Cloud API Demo checkout, local logs, Docker logs, local
HTTP requests, deployment scanning, and one-use authenticated local debug
requests when deployment credentials are supplied in chat.

## Network Layout

```text
LAN browser
  -> http://192.168.0.3:7860
  -> Streamlit chat-web
  -> http://127.0.0.1:8642/v1/chat/completions
  -> Hermes Agent API server
  -> dikong_code_context toolset
```

Hermes API server is bound to localhost only:

```text
127.0.0.1:8642
```

The frontend-facing Streamlit page is bound to the LAN:

```text
0.0.0.0:7860
```

## Start

Start Hermes gateway/API server:

```bash
nohup hermes gateway run --accept-hooks \
  > /home/charles/.hermes/logs/gateway.log 2>&1 &
echo $! > /home/charles/.hermes/gateway.pid
```

Start the Streamlit Web UI:

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

Open from another machine on the LAN:

```text
http://192.168.0.3:7860
```

If the LAN IP changes:

```bash
hostname -I | awk '{print $1}'
```

## Stop

```bash
kill "$(cat /home/charles/.hermes/dikong-debug-chat.pid)"
kill "$(cat /home/charles/.hermes/gateway.pid)"
```

## Verify

```bash
hermes version
hermes skills list
curl -fsS http://127.0.0.1:8642/health
curl -fsS http://127.0.0.1:7860/ | head
docs/debug-agent/chat-web/.venv/bin/python -m unittest -v docs/debug-agent/chat-web/test_server.py
```

End-to-end backend call:

```bash
docs/debug-agent/chat-web/.venv/bin/python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path("docs/debug-agent/chat-web").resolve()))
import server

config = server.load_config()
store = server.SessionStore(Path("/tmp/dikong-debug-chat-real-test.json"))
history, session_id, choices = server.send_message("只回答：web-chat-ok", "", config, store)
print(history[-1]["content"])
PY
```

Expected output:

```text
web-chat-ok
```

## Sessions

The Web UI supports creating, renaming, switching, and deleting sessions. Session
data is stored locally:

```text
/home/charles/.hermes/dikong-debug-chat/sessions.json
```
