"""
agent.py — Morning Briefing Agent (LangGraph, explicit graph)
-------------------------------------------------------------
Demonstrates how to wire LangChain tools, an MCP server, and a SKILL file
together inside an explicit LangGraph StateGraph — without collapsing each
tool into its own node.

Graph topology:

  START → brief_agent ──(tool calls?)──► tools → brief_agent → ...
                       └─(no tool calls)──► END

Nodes:
  brief_agent  — LLM (ChatOpenAI) with all tools bound; SKILL.md is injected
                 as a system prompt so the model writes to the style guide.
  tools        — ToolNode that dispatches every tool call the LLM emits,
                 regardless of whether it came from tools.py or the MCP server.

Tools (registered on ToolNode — the LLM decides when to call them):
  get_current_date, get_weather, generate_pdf_briefing  — tools.py
  get_top_headlines, search_news                         — GNews MCP server

Usage:
    python agent.py
    python agent.py --city "New York" --lat 40.71 --lon -74.01

Requires: OPENAI_API_KEY and GNEWS_API_KEY in .env
Skill:    SKILL.md in the project root (Briefing Style Guide)
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tools import generate_pdf_briefing, get_current_date, get_weather

load_dotenv()

MODEL = "gpt-4o"
_SKILL_PATH = os.path.join(os.path.dirname(__file__), "SKILL.md")


# ── Skill ──────────────────────────────────────────────────────────────────────

def load_skill() -> str:
    if not os.path.exists(_SKILL_PATH):
        print("WARNING: SKILL.md not found — style guide not loaded.")
        return ""
    with open(_SKILL_PATH) as f:
        return f.read()


# ── System prompt ──────────────────────────────────────────────────────────────

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


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph(llm, all_tools: list, system_prompt: str):
    """Construct and compile the LangGraph StateGraph.

    Two nodes:
      brief_agent — LLM with all tools bound; system prompt carries SKILL.md.
      tools       — ToolNode that executes any tool call the LLM emits
                    (both tools.py tools and MCP tools are registered here).

    The graph loops brief_agent → tools → brief_agent until the LLM stops
    emitting tool calls, then routes to END.
    """
    llm_with_tools = llm.bind_tools(all_tools)
    tool_node = ToolNode(all_tools)
    system_msg = SystemMessage(content=system_prompt)

    # ── Node: brief_agent ──────────────────────────────────────────────────────
    # The LLM sees the SKILL.md system prompt on every turn and decides which
    # tools to call next (from tools.py or the MCP server — it doesn't matter).
    async def brief_agent(state: MessagesState):
        response = await llm_with_tools.ainvoke([system_msg] + state["messages"])
        return {"messages": [response]}

    # ── Routing ────────────────────────────────────────────────────────────────
    def route_after_agent(state: MessagesState) -> str:
        """Continue to ToolNode if the LLM emitted tool calls, otherwise end."""
        if state["messages"][-1].tool_calls:
            return "tools"
        return END

    # ── Assemble graph ─────────────────────────────────────────────────────────
    graph = StateGraph(MessagesState)

    graph.add_node("brief_agent", brief_agent)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "brief_agent")
    graph.add_conditional_edges("brief_agent", route_after_agent)
    graph.add_edge("tools", "brief_agent")

    return graph.compile()


# ── Main ───────────────────────────────────────────────────────────────────────

async def run_agent(city: str, latitude: float, longitude: float) -> None:
    openai_api_key = os.getenv("OPENAI_API_KEY")
    gnews_api_key  = os.getenv("GNEWS_API_KEY")

    if not openai_api_key:
        print("ERROR: OPENAI_API_KEY not set in .env"); sys.exit(1)
    if not gnews_api_key:
        print("ERROR: GNEWS_API_KEY not set in .env"); sys.exit(1)

    skill = load_skill()
    if skill:
        print("Skill loaded: SKILL.md (Briefing Style Guide)")

    llm = ChatOpenAI(model=MODEL, api_key=openai_api_key)
    system_prompt = build_system_prompt(skill)

    print("Starting GNews MCP server...")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["gnews_mcp_server.py"],
        env={**os.environ, "GNEWS_API_KEY": gnews_api_key},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()

            mcp_tools = await load_mcp_tools(mcp_session)
            all_tools = [get_current_date, get_weather, generate_pdf_briefing, *mcp_tools]

            print(f"Tools available: {[t.name for t in all_tools]}")
            print(f"\nGenerating morning briefing for {city}...\n")
            print("─" * 50)

            app = build_graph(llm, all_tools, system_prompt)

            result = await app.ainvoke({
                "messages": [
                    HumanMessage(content=(
                        f"Generate my morning briefing. "
                        f"I'm in {city} (lat={latitude}, lon={longitude})."
                    ))
                ]
            })

            final_msg = result["messages"][-1]
            if hasattr(final_msg, "content") and final_msg.content:
                print(final_msg.content)

            print("─" * 50)
            print("\nCheck the output/ folder for your PDF.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Morning Briefing Agent (LangGraph)")
    parser.add_argument("--city", default="Singapore", help="City name")
    parser.add_argument("--lat",  type=float, default=1.29,   help="Latitude")
    parser.add_argument("--lon",  type=float, default=103.85, help="Longitude")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_agent(city=args.city, latitude=args.lat, longitude=args.lon))
