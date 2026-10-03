from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

try:
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        ImageContent,
        TextContent,
        Tool,
    )
except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent.
    raise RuntimeError(
        'PASI Agent MCP requires the optional "mcp" dependency. '
        'Install it with: pip install "mcp[cli]"'
    ) from exc

from automation.pasi_agent_contracts import (
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_DESCRIPTIONS,
    TOOL_INPUT_SCHEMAS,
    TOOL_NAMES,
    TOOL_OUTPUT_SCHEMAS,
    PasiAgentObservationService,
)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            input_schema=TOOL_INPUT_SCHEMAS[name],
            output_schema=TOOL_OUTPUT_SCHEMAS[name],
        )
        for name in TOOL_NAMES
    ]


async def list_tools(
    _ctx: ServerRequestContext,
    _params: PaginatedRequestParams | None,
) -> ListToolsResult:
    return ListToolsResult(tools=build_tools())


def _request_id(ctx: ServerRequestContext) -> str:
    value = getattr(ctx, "request_id", None)
    return str(value) if value is not None else ""


async def call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    arguments = dict(params.arguments or {})
    arguments["_request_id"] = _request_id(ctx)
    service = PasiAgentObservationService()
    result = service.observe(params.name, arguments)
    structured_result = result
    content: list[Any] = []
    screenshot = None
    if result.get("ok") is True and isinstance(result.get("data"), dict):
        if params.name == "pasi.get_browser_screenshot":
            candidate = result["data"].get("screenshot")
            if isinstance(candidate, dict):
                screenshot = candidate
        elif params.name == "pasi.run_browser_test":
            candidate = result["data"].get("result")
            if isinstance(candidate, dict) and candidate.get("kind") == "screenshot":
                screenshot = candidate
    if isinstance(screenshot, dict):
        image_data = screenshot.get("image_base64")
        if isinstance(image_data, str) and image_data:
            structured_result = json.loads(json.dumps(result))
            structured_result["data"]["screenshot"].pop("image_base64", None)
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(structured_result, ensure_ascii=False, separators=(",", ":")),
                )
            )
            content.append(
                ImageContent(
                    type="image",
                    data=image_data,
                    mimeType="image/png",
                )
            )
        else:
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                )
            )
    else:
        content.append(
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            )
        )
    return CallToolResult(
        content=content,
        structured_content=structured_result,
        is_error=not bool(result.get("ok")),
    )


def build_server() -> Server:
    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def run_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def create_streamable_http_app() -> Any:
    """Build the same observation-only MCP server as a Streamable HTTP ASGI app.

    Authentication is deliberately not added here yet. The intended deployment
    boundary is a local Secure MCP Tunnel/native host or another explicit
    authenticated transport, not an unauthenticated TCP listener.
    """
    return build_server().streamable_http_app()


def main() -> int:
    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
