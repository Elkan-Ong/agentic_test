"""
agent.py — Morning Briefing Agent
-----------------------------------
Orchestrates the full briefing pipeline:

  1. Spawns the GNews MCP server as a subprocess (stdio transport)
  2. Loads MCP tools from the server + defines 3 custom tools
  3. Sends all tools to Claude and runs the agentic tool-use loop
  4. Claude calls tools in order → date → headlines → tech news → weather → PDF
  5. Saves a formatted PDF to ./output/

Usage:
    python agent.py
    python agent.py --city "New York" --lat 40.71 --lon -74.01

Requires: ANTHROPIC_API_KEY and GNEWS_API_KEY in .env
"""

import argparse
import asyncio
import json
import os
import sys

#import anthropic
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.messages import ToolMessage
import json

from tools import get_current_date, get_weather, generate_pdf_briefing

load_dotenv()

#MODEL = "claude-sonnet-4-6"

# ── System prompt ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a morning briefing assistant. Compile a clean daily briefing by calling tools in this order:

1. get_current_date — get today's date
2. get_top_headlines — fetch 5 world/general headlines (topic="world")
3. search_news — search for "technology AI" to get 4 tech stories
4. get_weather — get weather for the user's city
5. generate_pdf_briefing — build the final PDF with these sections:
     • "🌤  Weather" — one-line weather summary
     • "📰  Top Headlines" — numbered list of headline + one-sentence description each
     • "💻  Tech Digest" — numbered list of tech stories, same format

Keep content concise and scannable. Only call generate_pdf_briefing once all content is ready.
After generating the PDF, confirm what was produced in a short friendly message.
"""

# ── Custom tool definitions (passed to Claude as tool schemas) ─────────────────
CUSTOM_TOOLS = [
    {
        "name": "get_current_date",
        "description": "Return today's date as a human-readable string.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_weather",
        "description": (
            "Fetch current weather conditions for a city. "
            "Uses Open-Meteo — free and requires no API key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city":      {"type": "string",  "description": "City display name"},
                "latitude":  {"type": "number",  "description": "Decimal latitude"},
                "longitude": {"type": "number",  "description": "Decimal longitude"},
            },
            "required": ["city", "latitude", "longitude"],
        },
    },
    {
        "name": "generate_pdf_briefing",
        "description": (
            "Generate a formatted A4 PDF morning briefing. "
            "Call this last, after all content has been gathered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Briefing title"},
                "date":  {"type": "string", "description": "Date string for the header"},
                "sections": {
                    "type": "array",
                    "description": "Ordered list of content sections",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["heading", "content"],
                    },
                },
            },
            "required": ["title", "date", "sections"],
        },
    },
]

CUSTOM_TOOL_NAMES = {t["name"] for t in CUSTOM_TOOLS}


# ── Custom tool dispatcher ─────────────────────────────────────────────────────

async def run_custom_tool(name: str, tool_input: dict) -> str:
    if name == "get_current_date":
        return get_current_date()
    elif name == "get_weather":
        return await get_weather(
            city=tool_input["city"],
            latitude=tool_input["latitude"],
            longitude=tool_input["longitude"],
        )
    elif name == "generate_pdf_briefing":
        return generate_pdf_briefing(
            title=tool_input["title"],
            date=tool_input["date"],
            sections=tool_input["sections"],
        )
    return f"Unknown tool: {name}"


# ── Main agent loop ────────────────────────────────────────────────────────────

async def run_agent(city: str, latitude: float, longitude: float) -> None:
    # Validate env vars
    anthropic_api_key = os.getenv("OPENAI_API_KEY")
    gnews_api_key = os.getenv("GNEWS_API_KEY")

    if not anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env"); sys.exit(1)
    if not gnews_api_key:
        print("ERROR: GNEWS_API_KEY not set in .env"); sys.exit(1)

    client = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=4096)
    

    # ── Start GNews MCP server ─────────────────────────────────────────────
    print("Starting GNews MCP server...")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["gnews_mcp_server.py"],
        env={**os.environ, "GNEWS_API_KEY": gnews_api_key},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()

            # ── Load MCP tools ─────────────────────────────────────────────
            mcp_tools_result = await mcp_session.list_tools()
            mcp_tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema,
                }
                for t in mcp_tools_result.tools
            ]

            all_tools = CUSTOM_TOOLS + mcp_tools
            print(f"Tools available: {[t['name'] for t in all_tools]}")
            print(f"\nGenerating morning briefing for {city}...\n")
            print("─" * 50)

            # ── Initial user message ───────────────────────────────────────
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"Generate my morning briefing. "
                        f"I'm in {city} (lat={latitude}, lon={longitude})."
                    ),
                }
            ]
            llm_with_tools = client.bind_tools(all_tools)

            # ── Agentic loop ───────────────────────────────────────────────
            while True:
                # OpenAI expects the system prompt inside the messages array
                payload_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

                response = llm_with_tools.invoke(payload_messages)
                # The response from .invoke() is already the AIMessage object
                assistant_message = response 

                # Append it directly to your history
                messages.append(assistant_message)

                # Check for tool calls (LangChain automatically parses these into a list)
                if not assistant_message.tool_calls:
                    break

                # ── Done ───────────────────────────────────────────────────
                # If there are no tool calls, the model is finished reasoning
                if not response.tool_calls:
                    # The text response is simply stored in response.content
                    if response.content:
                        print(response.content)
                    break

                # ── Tool use ───────────────────────────────────────────────
                # LangChain already parsed the tool requests into a nice list of dicts!
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    
                    # LangChain automatically parses the arguments into a Python dictionary
                    tool_args = tool_call["args"] 
                    tool_id = tool_call["id"]

                    # Pretty-print progress
                    args_preview = json.dumps(tool_args, ensure_ascii=False)
                    if len(args_preview) > 80:
                        args_preview = args_preview[:77] + "..."
                    print(f"  → {tool_name}({args_preview})")

                    try:
                        if tool_name in CUSTOM_TOOL_NAMES:
                            # Note: pass the dict directly, no need for block.input
                            result = await run_custom_tool(tool_name, tool_args) 
                        else:
                            mcp_resp = await mcp_session.call_tool(
                                tool_name, tool_args
                            )
                            result = (
                                mcp_resp.content[0].text
                                if mcp_resp.content
                                else "No result."
                            )
                    except Exception as exc:
                        result = f"Tool error ({tool_name}): {exc}"
                        print(f"     ERROR: {exc}")

                    # Append the result as a LangChain ToolMessage
                    messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_id
                        )
                    )

            print("─" * 50)
            print("\nCheck the output/ folder for your PDF.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Morning Briefing Agent")
    parser.add_argument("--city",  default="Singapore",  help="City name")
    parser.add_argument("--lat",   type=float, default=1.29, help="Latitude")
    parser.add_argument("--lon",   type=float, default=103.85, help="Longitude")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_agent(city=args.city, latitude=args.lat, longitude=args.lon))
