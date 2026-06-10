# Morning Briefing Agent

A small agentic application that uses Claude, a GNews MCP server, and custom tools
to compile a daily news + weather briefing and export it as a formatted PDF.

---

## Architecture

```
agent.py  (orchestrator)
  │
  ├─ Custom tools (tools.py)
  │     ├─ get_current_date     → datetime
  │     ├─ get_weather          → Open-Meteo API (free, no key)
  │     └─ generate_pdf_briefing → reportlab PDF
  │
  └─ MCP server (gnews_mcp_server.py)  ← spawned via stdio
        ├─ get_top_headlines    → GNews API
        └─ search_news          → GNews API
```

**Agent flow:**
1. Agent spawns the GNews MCP server as a subprocess
2. Loads all tool schemas and passes them to Claude
3. Claude calls tools in sequence (date → headlines → tech news → weather)
4. Claude calls `generate_pdf_briefing` with all compiled content
5. PDF is saved to `./output/`

---

## Prerequisites

- Python 3.11+
- A free [GNews API key](https://gnews.io) (100 req/day on free tier)
- An [Anthropic API key](https://console.anthropic.com)

---

## Setup

```bash
# 1. Clone / copy the project folder
cd morning-briefing-agent

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and paste your ANTHROPIC_API_KEY and GNEWS_API_KEY
```

---

## Running

```bash
# Default: Singapore
python agent.py

# Custom city
python agent.py --city "London" --lat 51.51 --lon -0.13

# New York
python agent.py --city "New York" --lat 40.71 --lon -74.01
```

The agent prints its tool calls as it runs, then saves a PDF to `./output/`.

---

## Example output

```
Starting GNews MCP server...
Tools available: ['get_current_date', 'get_weather', 'generate_pdf_briefing',
                  'get_top_headlines', 'search_news']

Generating morning briefing for Singapore...
──────────────────────────────────────────────────
  → get_current_date({})
  → get_top_headlines({"topic": "world", "max_results": 5})
  → search_news({"query": "technology AI", "max_results": 4})
  → get_weather({"city": "Singapore", "latitude": 1.29, "longitude": 103.85})
  → generate_pdf_briefing({"title": "Morning Briefing", "date": "Wednesday, Jun...})
──────────────────────────────────────────────────
Your morning briefing is ready! Saved as a PDF in the output/ folder.

Check the output/ folder for your PDF.
```

---

## Skills & libraries used

| Component | Skill / Library |
|---|---|
| PDF generation | `reportlab` — `SimpleDocTemplate` + `Platypus` |
| MCP server | `mcp` Python SDK — `FastMCP` (stdio transport) |
| Weather | Open-Meteo REST API (free, no key) |
| News | GNews REST API (free tier) |
| Agent loop | `anthropic` Python SDK — tool use |

---

## Project structure

```
morning-briefing-agent/
├── agent.py              # Orchestrator — spawns MCP, runs agent loop
├── gnews_mcp_server.py   # GNews MCP server (2 tools)
├── tools.py              # Custom tools (date, weather, PDF)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Customising

- **Add more news topics** — edit the SYSTEM_PROMPT in `agent.py` to instruct Claude to call `search_news` with different queries (e.g. "finance markets", "climate energy")
- **Change city** — pass `--city`, `--lat`, `--lon` flags or hard-code defaults in `agent.py`
- **Adjust PDF styling** — edit the `ParagraphStyle` definitions in `tools.py`
- **Add more MCP tools** — add `@mcp.tool()` decorated functions in `gnews_mcp_server.py`
