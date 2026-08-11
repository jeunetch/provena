"""Static HTML report renderer.

The report is one self-contained file with no build step and no external
requests: the screening result is embedded as JSON and rendered client-side
from the template in report/report_template.html.

The template renders record values through DOM text nodes rather than
innerHTML, so supplied record data cannot inject markup into the report.
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "report/report_template.html"
DATA_SENTINEL = "__TRIAGE_DATA__"


def _embed(data: dict) -> str:
    """Serialise for inclusion in a <script type="application/json"> block.

    `\\/` and `\\u003c` are valid JSON escapes, so escaping here cannot break
    the parse but does stop a record value containing `</script>` from ending
    the block early.
    """
    text = json.dumps(data, ensure_ascii=False)
    return text.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def render(data: dict, template_path: str | Path = TEMPLATE_PATH) -> str:
    """Render a screening result into a self-contained HTML document."""
    template = Path(template_path).read_text(encoding="utf-8")
    if DATA_SENTINEL not in template:
        raise ValueError(f"template {template_path} has no {DATA_SENTINEL} placeholder")
    return template.replace(DATA_SENTINEL, _embed(data))
