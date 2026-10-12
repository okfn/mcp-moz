"""A minimal Starlette chat app backed by the local MCP server and a chat model.

The MCP server (server.py) already runs as streamable-http at http://localhost:8000/mcp.
This app is an agent loop between the user, a chat model (GLM via the OpenAI-compatible
z.ai API, configured through .env / environment variables) and the MCP server:

    user message -> model (with the MCP tools as function definitions)
                 -> model asks for tool calls -> executed over MCP
                 -> results fed back to the model -> final reply

When the model calls a tool that carries ``_meta.ui.resourceUri`` (the MCP Apps
extension, ``io.modelcontextprotocol/ui``), the tool result is returned to the
front end as an "artifact". The front end loads the ``ui://`` HTML resource into
a sandboxed iframe and delivers the tool result to it via ``postMessage`` as a
``notifications/call_tool_result`` JSON-RPC notification (the payload shape the
resource expects: ``event.data.params.structuredContent``), rendering the chart
inline in the conversation.

Routes:

* GET  /            -> serves index.html (the chat UI)
* POST /api/chat    -> body: {messages: [{role, content}...]} (user/assistant turns)
                       returns {reply, artifacts: [{resourceUri, result}]}
* GET  /ui-resource -> proxies a ``ui://`` resource (read via resources/read) so the
                       iframe can load it by URL

Configuration (env or .env): API_KEY, AI_MODEL, AI_BASE_URL, MCP_SERVER_URL.

Run with:  uv run uvicorn app:app --port 8080
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2
import uvicorn
from dotenv import load_dotenv
from mcp import Client
from mcp.types import CallToolResult, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
MODEL = os.environ.get("AI_MODEL")
GLM_BASE_URL = os.environ.get("AI_BASE_URL")
API_KEY = os.environ.get("API_KEY", "")

INDEX_HTML = HERE / "index.html"

SYSTEM_PROMPT = (
    "You are a helpful assistant connected to an MCP server with tools."
    "Call the available tools whenever they help answer the user's question,"
    "then summarize the results."
    "Reply in the user's language and with text (no markdown)."

)

MAX_TOOL_ROUNDS = 4


def openai_tools(tools: list[Tool]) -> list[dict]:
    """Translate MCP tool definitions into OpenAI-style function definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


async def index(request: Request) -> FileResponse:
    """Serve the static chat front end."""
    return FileResponse(INDEX_HTML)


async def chat(request: Request) -> JSONResponse:
    """Run the agent loop: model -> MCP tool calls -> final reply + UI artifacts."""
    try:
        body = await request.json()
        incoming = body.get("messages", [])
    except Exception as exc:  # noqa: BLE001 - malformed request body
        return JSONResponse({"error": f"Bad request body: {exc}"}, status_code=400)

    conversation: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in incoming:
        if message.get("role") in ("user", "assistant") and message.get("content"):
            conversation.append({"role": message["role"], "content": str(message["content"])})

    artifacts: list[dict] = []
    try:
        async with Client(MCP_SERVER_URL) as mcp_client:
            tools = (await mcp_client.list_tools()).tools
            resource_by_tool = {
                tool.name: (tool.meta or {}).get("ui", {}).get("resourceUri") for tool in tools
            }
            schemas = openai_tools(tools)

            async with httpx2.AsyncClient(
                base_url=GLM_BASE_URL,
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=120,
            ) as model:
                for _ in range(MAX_TOOL_ROUNDS):
                    response = await model.post(
                        "/chat/completions",
                        json={
                            "model": MODEL,
                            "messages": conversation,
                            "tools": schemas,
                            "tool_choice": "auto",
                        },
                    )
                    if response.status_code != 200:
                        return JSONResponse(
                            {"error": f"Model API error {response.status_code}: {response.text[:500]}"},
                            status_code=502,
                        )

                    message = response.json()["choices"][0]["message"]
                    conversation.append(message)

                    tool_calls = message.get("tool_calls") or []
                    if not tool_calls:
                        return JSONResponse({"reply": message.get("content") or "", "artifacts": artifacts})

                    for tool_call in tool_calls:
                        name = tool_call["function"]["name"]
                        try:
                            arguments = json.loads(tool_call["function"].get("arguments") or "{}")
                        except json.JSONDecodeError:
                            arguments = {}

                        result: CallToolResult = await mcp_client.call_tool(name, arguments)
                        # Spec-shaped CallToolResult params for notifications/call_tool_result.
                        params = {
                            k: v
                            for k, v in result.model_dump(
                                by_alias=True, exclude_none=True, mode="json"
                            ).items()
                            if k in ("content", "structuredContent", "isError")
                        }

                        resource_uri = resource_by_tool.get(name)
                        if resource_uri:
                            artifacts.append({"resourceUri": resource_uri, "result": params})

                        tool_output = (
                            params.get("structuredContent")
                            if params.get("structuredContent") is not None
                            else params.get("content")
                        )
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", name),
                            "content": json.dumps(tool_output, ensure_ascii=False),
                        })

        return JSONResponse({
            "reply": "Reached the tool-call limit without a final answer.",
            "artifacts": artifacts,
        })
    except Exception as exc:  # noqa: BLE001 - simple test client
        return JSONResponse({"error": f"Chat failed: {exc}"}, status_code=500)


async def ui_resource(request: Request) -> HTMLResponse | JSONResponse:
    """Proxy a ui:// resource from the MCP server so the iframe can load it."""
    uri = request.query_params.get("uri", "")
    if not uri.startswith("ui://"):
        return JSONResponse({"error": f"Not a ui:// resource: {uri}"}, status_code=400)

    try:
        async with Client(MCP_SERVER_URL) as client:
            result = await client.read_resource(uri)
    except Exception as exc:  # noqa: BLE001 - simple test client
        return JSONResponse({"error": f"Could not read resource {uri}: {exc}"}, status_code=502)

    html = next((c.text for c in result.contents if getattr(c, "text", None)), None)
    if html is None:
        return JSONResponse({"error": f"Resource {uri} has no text content"}, status_code=404)
    return HTMLResponse(html)


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/ui-resource", ui_resource, methods=["GET"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
