from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .memory import MemoryStore


def run_dashboard(memory_db: Path, host: str, port: int) -> None:
    store = MemoryStore(memory_db)
    store.initialize()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            body = render_dashboard(store).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"coding-agents-mem dashboard: http://{host}:{port}", flush=True)
    server.serve_forever()


def render_dashboard(store: MemoryStore) -> str:
    snapshot = store.dashboard_snapshot(hours=24, limit=12)
    counts = snapshot["counts"]
    usage = snapshot["usage"]
    totals = usage["totals"]
    health = usage["health"]

    top_cards = [
        ("Estimated cost (24h)", _fmt_usd(totals.get("cost_usd")), "API-equivalent estimate"),
        ("Total tokens (24h)", _fmt_int(totals.get("total_tokens")), f"{_fmt_int(totals.get('usage_events'))} usage events"),
        ("Input tokens", _fmt_int(totals.get("input_tokens_total")), f"{_fmt_int(totals.get('cache_read_input_tokens'))} cached"),
        ("Output tokens", _fmt_int(totals.get("output_tokens")), f"{_fmt_int(totals.get('reasoning_tokens'))} reasoning"),
        ("Active sessions", _fmt_int(health.get("active_sessions")), f"{_fmt_int(health.get('tool_calls'))} tool calls"),
        ("Trace health", _fmt_percent(health.get("error_rate")), f"{_fmt_int(health.get('error_events'))} error events"),
    ]
    metric_cards = "\n".join(
        f"""
        <article class="metric-card">
          <span class="eyebrow">{html.escape(title)}</span>
          <strong>{html.escape(value)}</strong>
          <span class="muted">{html.escape(subtitle)}</span>
        </article>
        """
        for title, value, subtitle in top_cards
    )

    count_cards = "\n".join(
        f"""
        <article class="mini-card">
          <span>{html.escape(key.replace('_', ' '))}</span>
          <strong>{value}</strong>
        </article>
        """
        for key, value in counts.items()
    )

    agent_mix = _render_rank_list(usage["by_agent"], "Agent spend", "name", "cost_usd", "total_tokens")
    model_mix = _render_rank_list(usage["by_model"], "Model mix", "name", "cost_usd", "total_tokens")
    project_mix = _render_rank_list(usage["by_project"], "Project mix", "name", "cost_usd", "total_tokens")

    session_rows = "\n".join(
        """
        <tr>
          <td>{last_timestamp}</td>
          <td>{agent}</td>
          <td>{project}</td>
          <td>{model}</td>
          <td>{cost}</td>
          <td>{tokens}</td>
        </tr>
        """.format(
            last_timestamp=html.escape(str(row.get("last_timestamp") or "")),
            agent=html.escape(str(row.get("agent") or "")),
            project=html.escape(str(row.get("project") or "")),
            model=html.escape(str(row.get("model") or "")),
            cost=html.escape(_fmt_usd(row.get("cost_usd"))),
            tokens=html.escape(_fmt_int(row.get("total_tokens"))),
        )
        for row in usage["recent_sessions"]
    )

    failure_rows = "\n".join(
        """
        <tr>
          <td>{timestamp}</td>
          <td>{agent}</td>
          <td>{project}</td>
          <td>{title}</td>
        </tr>
        """.format(
            timestamp=html.escape(str(row.get("timestamp") or "")),
            agent=html.escape(str(row.get("agent") or "")),
            project=html.escape(str(row.get("project") or "")),
            title=html.escape(str(row.get("title") or "")),
        )
        for row in usage["recent_failures"]
    )

    event_rows = "\n".join(
        """
        <tr>
          <td>{timestamp}</td>
          <td>{agent}</td>
          <td>{project}</td>
          <td>{title}</td>
          <td>{detail}</td>
          <td>{tokens}</td>
          <td>{cost}</td>
        </tr>
        """.format(
            timestamp=html.escape(str(row.get("timestamp") or "")),
            agent=html.escape(str(row.get("agent") or "")),
            project=html.escape(str(row.get("project") or "")),
            title=html.escape(str(row.get("title") or "")),
            detail=html.escape(str(row.get("detail") or "")),
            tokens=html.escape(_fmt_int((row.get("measurements") or {}).get("total_tokens"))),
            cost=html.escape(_fmt_usd((row.get("measurements") or {}).get("cost_usd"))),
        )
        for row in snapshot["recent_events"]
    )

    memory_rows = "\n".join(
        """
        <tr>
          <td>{created_at}</td>
          <td>{agent}</td>
          <td>{project}</td>
          <td>{type}</td>
          <td>{title}</td>
        </tr>
        """.format(
            created_at=html.escape(str(row.get("created_at") or "")),
            agent=html.escape(str(row.get("agent") or "")),
            project=html.escape(str(row.get("project") or "")),
            type=html.escape(str(row.get("type") or "")),
            title=html.escape(str(row.get("title") or "")),
        )
        for row in snapshot["recent_memories"]
    )

    pricing_notes = "\n".join(f"<li>{html.escape(note)}</li>" for note in usage["pricing_notes"]) or "<li>No pricing caveats recorded.</li>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>coding-agents-mem</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #08111a;
      --panel: #101e28;
      --panel-2: #152733;
      --line: #28404e;
      --text: #edf4f2;
      --muted: #96b0ad;
      --teal: #41c3b2;
      --amber: #f7b955;
      --salmon: #ff7b72;
      --mint: #8ad4a8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, sans-serif;
      background: linear-gradient(180deg, #08111a 0%, #0d1822 42%, #08111a 100%);
      color: var(--text);
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      background: rgba(6, 14, 20, 0.84);
      position: sticky;
      top: 0;
      backdrop-filter: blur(12px);
    }}
    header h1 {{ margin: 0; font-size: 28px; }}
    header p {{ margin: 8px 0 0; color: var(--muted); max-width: 960px; }}
    main {{ padding: 24px 32px 56px; display: grid; gap: 20px; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .metric-card, .mini-card, .panel {{
      background: linear-gradient(180deg, rgba(22,39,51,0.94), rgba(12,24,34,0.96));
      border: 1px solid rgba(124, 171, 166, 0.18);
      border-radius: 8px;
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
    }}
    .metric-card {{
      padding: 16px;
      display: grid;
      gap: 6px;
      min-height: 114px;
    }}
    .metric-card strong {{ font-size: 28px; letter-spacing: 0; }}
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 10px;
    }}
    .mini-card {{
      padding: 12px 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }}
    .mini-card span, .eyebrow, .muted {{ color: var(--muted); }}
    .eyebrow {{
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.04em;
    }}
    .panels {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
    }}
    .panel {{ padding: 16px; }}
    .panel h2 {{ margin: 0 0 14px; font-size: 17px; }}
    .rank-list {{
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .rank-list li {{ display: grid; gap: 6px; }}
    .rank-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }}
    .bar {{
      height: 9px;
      border-radius: 6px;
      background: rgba(255,255,255,0.08);
      overflow: hidden;
    }}
    .bar > span {{
      display: block;
      height: 100%;
      border-radius: 6px;
      background: linear-gradient(90deg, var(--teal), var(--amber));
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
    }}
    thead th {{
      text-align: left;
      color: var(--muted);
      font-size: 12px;
      padding-bottom: 10px;
    }}
    td {{
      border-top: 1px solid rgba(255,255,255,0.06);
      padding: 10px 0;
      vertical-align: top;
    }}
    .table-panel {{ overflow: auto; }}
    .notes {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .notes li + li {{ margin-top: 8px; }}
    .accent-cost {{ color: var(--amber); }}
    .accent-ok {{ color: var(--mint); }}
    .accent-warn {{ color: var(--salmon); }}
    @media (max-width: 720px) {{
      header, main {{ padding-left: 18px; padding-right: 18px; }}
      .metric-card strong {{ font-size: 24px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>coding-agents-mem</h1>
    <p>Shared observability and memory for Codex, Claude Code, and Pi. Window: last {usage['window_hours']} hours. Legacy imports tracked: {usage['import_status']['claude_mem_sources']} source rows.</p>
  </header>
  <main>
    <section class="metric-grid">{metric_cards}</section>
    <section class="mini-grid">{count_cards}</section>
    <section class="panels">
      <div class="panel">{agent_mix}</div>
      <div class="panel">{model_mix}</div>
      <div class="panel">{project_mix}</div>
    </section>
    <section class="panels">
      <div class="panel table-panel">
        <h2>Recent sessions</h2>
        <table>
          <thead><tr><th>Time</th><th>Agent</th><th>Project</th><th>Model</th><th>Cost</th><th>Tokens</th></tr></thead>
          <tbody>{session_rows or '<tr><td colspan="6">No recent usage sessions.</td></tr>'}</tbody>
        </table>
      </div>
      <div class="panel table-panel">
        <h2>Recent failures</h2>
        <table>
          <thead><tr><th>Time</th><th>Agent</th><th>Project</th><th>Trace</th></tr></thead>
          <tbody>{failure_rows or '<tr><td colspan="4">No recent failures.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panels">
      <div class="panel table-panel">
        <h2>Recent event stream</h2>
        <table>
          <thead><tr><th>Time</th><th>Agent</th><th>Project</th><th>Trace</th><th>Detail</th><th>Tokens</th><th>Cost</th></tr></thead>
          <tbody>{event_rows or '<tr><td colspan="7">No recent events.</td></tr>'}</tbody>
        </table>
      </div>
      <div class="panel table-panel">
        <h2>Recent memories</h2>
        <table>
          <thead><tr><th>Time</th><th>Agent</th><th>Project</th><th>Type</th><th>Title</th></tr></thead>
          <tbody>{memory_rows or '<tr><td colspan="5">No memories available.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Pricing notes</h2>
      <ul class="notes">{pricing_notes}</ul>
    </section>
  </main>
</body>
</html>"""


def _render_rank_list(rows: list[dict[str, Any]], title: str, label_key: str, value_key: str, secondary_key: str) -> str:
    if not rows:
        return f"<h2>{html.escape(title)}</h2><p class='muted'>No data yet.</p>"
    max_value = max(float(row.get(value_key) or 0) for row in rows) or 1.0
    items = []
    for row in rows:
        value = float(row.get(value_key) or 0)
        width = max(6.0, round(value * 100 / max_value, 1)) if value else 6.0
        items.append(
            """
            <li>
              <div class="rank-head">
                <strong>{label}</strong>
                <span class="accent-cost">{value}</span>
              </div>
              <div class="bar"><span style="width:{width}%"></span></div>
              <div class="muted">{secondary}</div>
            </li>
            """.format(
                label=html.escape(str(row.get(label_key) or "unknown")),
                value=html.escape(_fmt_usd(value)),
                width=width,
                secondary=html.escape(f"{_fmt_int(row.get(secondary_key))} tokens"),
            )
        )
    return f"<h2>{html.escape(title)}</h2><ol class='rank-list'>{''.join(items)}</ol>"


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(round(float(value or 0))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_usd(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount >= 1:
        return f"${amount:,.2f}"
    if amount > 0:
        return f"${amount:,.4f}"
    return "$0.00"


def _fmt_percent(value: Any) -> str:
    try:
        ratio = float(value or 0)
    except (TypeError, ValueError):
        ratio = 0.0
    return f"{ratio * 100:.1f}%"
