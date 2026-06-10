"""
agent.py — Morning Briefing Agent (OpenAI)
-------------------------------------------
Orchestrates the full briefing pipeline:

  1. Loads SKILL.md from the project root and injects it into the system prompt
  2. Spawns the GNews MCP server as a subprocess (stdio transport)
  3. Combines MCP tools + custom tools and passes them to the model
  4. Runs the OpenAI agentic tool-use loop
  5. Model calls tools: date → headlines → tech news → weather → PDF
  6. Saves a formatted PDF to ./output/

Usage:
    python agent.py
    python agent.py --city "New York" --lat 40.71 --lon -74.01

Requires: OPENAI_API_KEY and GNEWS_API_KEY in .env
Skill:    SKILL.md in the project root (Briefing Style Guide)
"""

import argparse
import asyncio
import json
import os
import sys

from openai import AsyncOpenAI
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tools import get_current_date, get_weather, generate_pdf_briefing

load_dotenv()

MODEL = "gpt-4o"

# ── Load SKILL.md ──────────────────────────────────────────────────────────────
# The skill is a Briefing Style Guide that the model reads before composing
# any section content. It actively shapes tone, length, formatting rules, and
# what to include or skip — this is where the skill earns its place at runtime.
_SKILL_PATH = os.path.join(os.path.dirname(__file__), "SKILL.md")

def load_skill() -> str:
    if not os.path.exists(_SKILL_PATH):
        print("WARNING: SKILL.md not found in project root — style guide not loaded.")
        return ""
    with open(_SKILL_PATH) as f:
        return f.read()


# ── System prompt ──────────────────────────────────────────────────────────────
# SKILL.md is appended here so the model has it in context for the entire run.
# Every time it writes section content it can refer back to the style rules.

def build_system_prompt(skill: str) -> str:
    base = """You are a morning briefing assistant. Compile a daily briefing by calling tools in this order:

1. get_current_date — get today's date
2. get_top_headlines — fetch 5 world headlines (topic="world")
3. search_news — search "technology AI" for 4 tech stories
4. get_weather — get weather for the user's city
5. generate_pdf_briefing — compile everything into a PDF with these sections:
     - "Weather" — weather summary
     - "Top Headlines" — world news
     - "Tech Digest" — technology news

Call generate_pdf_briefing only once all content is gathered.
When writing the content for each section, follow the style guide below exactly.
After the PDF is saved, print a short confirmation message."""

    if skill:
        return base + "\n\n---\n\n" + skill
    return base


# ── Custom tool definitions ────────────────────────────────────────────────────
# OpenAI tool format: {"type": "function", "function": {"name", "description", "parameters"}}
# Note: MCP uses "input_schema" but OpenAI uses "parameters" — both are JSON Schema,
# so we rename the key when converting MCP tools below.

CUSTOM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "Return today's date as a human-readable string.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch current weather for a city via Open-Meteo (free, no API key).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city":      {"type": "string",  "description": "City display name"},
                    "latitude":  {"type": "number",  "description": "Decimal latitude"},
                    "longitude": {"type": "number",  "description": "Decimal longitude"},
                },
                "required": ["city", "latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pdf_briefing",
            "description": "Generate a formatted A4 PDF morning briefing. Call this last, after all content is ready.",
            "parameters": {
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
    },
]

CUSTOM_TOOL_NAMES = {t["function"]["name"] for t in CUSTOM_TOOLS}


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
    openai_api_key = os.getenv("OPENAI_API_KEY")
    gnews_api_key  = os.getenv("GNEWS_API_KEY")

    if not openai_api_key:
        print("ERROR: OPENAI_API_KEY not set in .env"); sys.exit(1)
    if not gnews_api_key:
        print("ERROR: GNEWS_API_KEY not set in .env"); sys.exit(1)

    client = AsyncOpenAI(api_key=openai_api_key)

    # ── Load skill ─────────────────────────────────────────────────────────
    skill = load_skill()
    if skill:
        print("Skill loaded: SKILL.md (Briefing Style Guide)")
    system_prompt = build_system_prompt(skill)

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

            # ── Convert MCP tools to OpenAI format ────────────────────────
            # MCP tools use "input_schema"; OpenAI expects "parameters".
            # Both are plain JSON Schema dicts, so it's just a key rename.
            mcp_tools_result = await mcp_session.list_tools()
            mcp_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema,
                    },
                }
                for t in mcp_tools_result.tools
            ]

            all_tools = CUSTOM_TOOLS + mcp_tools
            print(f"Tools available: {[t['function']['name'] for t in all_tools]}")
            print(f"\nGenerating morning briefing for {city}...\n")
            print("─" * 50)

            # ── Initial message ────────────────────────────────────────────
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"Generate my morning briefing. "
                        f"I'm in {city} (lat={latitude}, lon={longitude})."
                    ),
                }
            ]

            # ── Agentic loop ───────────────────────────────────────────────
            while True:
                response = await client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    tools=all_tools,
                    tool_choice="auto",
                )

                msg = response.choices[0].message
                finish_reason = response.choices[0].finish_reason

                # Append assistant turn
                messages.append(msg.model_dump(exclude_unset=False))

                # ── Done ───────────────────────────────────────────────────
                if finish_reason == "stop":
                    if msg.content:
                        print(msg.content)
                    break

                # ── Tool calls ─────────────────────────────────────────────
                if finish_reason == "tool_calls" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_input = json.loads(tc.function.arguments)

                        args_preview = tc.function.arguments
                        if len(args_preview) > 80:
                            args_preview = args_preview[:77] + "..."
                        print(f"  → {tc.function.name}({args_preview})")

                        try:
                            if tc.function.name in CUSTOM_TOOL_NAMES:
                                result = await run_custom_tool(tc.function.name, tool_input)
                            else:
                                # Pause before each GNews API call to avoid hitting
                                # the free tier rate limit (1 request/second)
                                await asyncio.sleep(1.5)
                                mcp_resp = await mcp_session.call_tool(
                                    tc.function.name, tool_input
                                )
                                result = (
                                    mcp_resp.content[0].text
                                    if mcp_resp.content
                                    else "No result."
                                )
                        except Exception as exc:
                            result = f"Tool error ({tc.function.name}): {exc}"
                            print(f"     ERROR: {exc}")

                        # OpenAI tool results use role="tool" + tool_call_id
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result),
                        })

            print("─" * 50)
            print("\nCheck the output/ folder for your PDF.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Morning Briefing Agent")
    parser.add_argument("--city", default="Singapore", help="City name")
    parser.add_argument("--lat",  type=float, default=1.29,   help="Latitude")
    parser.add_argument("--lon",  type=float, default=103.85, help="Longitude")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_agent(city=args.city, latitude=args.lat, longitude=args.lon))