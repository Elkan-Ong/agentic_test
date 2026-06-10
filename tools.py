"""
tools.py — Custom Agent Tools
------------------------------
Three tools the agent can call directly (not via MCP):

  get_current_date()          → formatted date string
  get_weather(...)            → weather via Open-Meteo (free, no API key)
  generate_pdf_briefing(...)  → creates a styled PDF using reportlab
"""

import os
from datetime import datetime

import httpx
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

# ── WMO weather-code descriptions (Open-Meteo standard) ──────────────────────
WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail",
}


# ── Tool 1: Date ──────────────────────────────────────────────────────────────

def get_current_date() -> str:
    """Return today's date as a human-readable string."""
    return datetime.now().strftime("%A, %B %d, %Y")


# ── Tool 2: Weather (Open-Meteo, free, no key) ────────────────────────────────

async def get_weather(city: str, latitude: float, longitude: float) -> str:
    """
    Fetch current weather for a city via the Open-Meteo API.
    Completely free — no API key required.

    Args:
        city:      Display name (e.g. "Singapore")
        latitude:  Decimal degrees
        longitude: Decimal degrees
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join([
            "temperature_2m",
            "apparent_temperature",
            "relative_humidity_2m",
            "weather_code",
            "wind_speed_10m",
        ]),
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        resp.raise_for_status()
        data = resp.json()

    cur = data["current"]
    condition = WMO_CODES.get(cur["weather_code"], "Unknown conditions")

    return (
        f"{city}: {condition}, {cur['temperature_2m']}°C "
        f"(feels like {cur['apparent_temperature']}°C) | "
        f"Humidity {cur['relative_humidity_2m']}% | "
        f"Wind {cur['wind_speed_10m']} km/h"
    )


# ── Tool 3: PDF generation (reportlab) ────────────────────────────────────────

def generate_pdf_briefing(title: str, date: str, sections: list[dict]) -> str:
    """
    Build a styled A4 PDF briefing document.

    Args:
        title:    Headline displayed at the top (e.g. "Morning Briefing")
        date:     Date string shown under the title
        sections: List of {"heading": str, "content": str} dicts

    Returns:
        Absolute path to the saved PDF.
    """
    os.makedirs("output", exist_ok=True)
    safe_date = date.replace(",", "").replace(" ", "_")
    output_path = os.path.abspath(f"output/morning_briefing_{safe_date}.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=inch,
        leftMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
    )

    # ── Styles ────────────────────────────────────────────────────────────────
    base = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "BriefingTitle",
        parent=base["Title"],
        fontSize=26,
        textColor=HexColor("#1a1a2e"),
        spaceAfter=4,
    )
    date_style = ParagraphStyle(
        "BriefingDate",
        parent=base["Normal"],
        fontSize=11,
        textColor=HexColor("#888888"),
        spaceAfter=18,
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=base["Heading2"],
        fontSize=13,
        textColor=HexColor("#16213e"),
        spaceBefore=20,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BriefingBody",
        parent=base["Normal"],
        fontSize=10,
        leading=16,
        textColor=HexColor("#333333"),
    )

    # ── Build story ───────────────────────────────────────────────────────────
    story = []

    story.append(Paragraph(title, title_style))
    story.append(Paragraph(date, date_style))
    story.append(HRFlowable(width="100%", thickness=2, color=HexColor("#1a1a2e")))
    story.append(Spacer(1, 10))

    for section in sections:
        story.append(Paragraph(section.get("heading", ""), heading_style))
        # Newlines → <br/> so reportlab renders line breaks inside Paragraph
        content = section.get("content", "").replace("\n", "<br/>")
        story.append(Paragraph(content, body_style))
        story.append(Spacer(1, 4))

    doc.build(story)
    return f"PDF saved → {output_path}"
