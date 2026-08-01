from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)


def load_sources() -> list[dict]:
    with (ROOT / "config" / "sources.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def scan_source(source: dict) -> dict:
    try:
        response = httpx.get(
            source["url"],
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "WarsawEventsPipeline/0.1"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else source["name"]
        return {
            "name": source["name"],
            "url": source["url"],
            "reliability": source["reliability"],
            "status": "ok",
            "page_title": title,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "name": source["name"],
            "url": source["url"],
            "reliability": source["reliability"],
            "status": "failed",
            "error": str(exc),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def main() -> None:
    results = [scan_source(source) for source in load_sources()]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(results),
        "successful": sum(item["status"] == "ok" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "sources": results,
    }
    (OUTPUT / "source_health.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Warsaw Events Pipeline Run",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Sources checked: {summary['sources_checked']}",
        f"Successful: {summary['successful']}",
        f"Failed: {summary['failed']}",
        "",
        "## Source status",
        "",
    ]
    for item in results:
        details = item.get("page_title") or item.get("error", "")
        lines.append(f"- **{item['name']}**: {item['status']} | {details} | {item['url']}")
    (OUTPUT / "run_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
