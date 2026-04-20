from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    print(f"Agent VM dashboard: http://{host}:{port}", flush=True)
    server.serve_forever()


def render_dashboard(store: MemoryStore) -> str:
    counts = store.counts()
    with store.connect() as conn:
        recent_events = conn.execute(
            """
            select e.title, a.name as agent, e.project, e.model, e.tool_name, e.timestamp
            from events e join agents a on a.id=e.agent_id
            order by e.timestamp_epoch desc
            limit 25
            """
        ).fetchall()
        recent_memories = conn.execute(
            """
            select project, type, title, created_at
            from memories
            order by created_at_epoch desc
            limit 20
            """
        ).fetchall()
    count_cards = "\n".join(
        f"<section><strong>{html.escape(key)}</strong><span>{value}</span></section>"
        for key, value in counts.items()
    )
    event_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['timestamp'] or '')}</td>"
        f"<td>{html.escape(row['agent'] or '')}</td>"
        f"<td>{html.escape(row['project'] or '')}</td>"
        f"<td>{html.escape(row['title'] or '')}</td>"
        f"<td>{html.escape(row['tool_name'] or row['model'] or '')}</td>"
        "</tr>"
        for row in recent_events
    )
    memory_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['created_at'] or '')}</td>"
        f"<td>{html.escape(row['project'] or '')}</td>"
        f"<td>{html.escape(row['type'] or '')}</td>"
        f"<td>{html.escape(row['title'] or '')}</td>"
        "</tr>"
        for row in recent_memories
    )
    counts_json = html.escape(json.dumps(counts, indent=2))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Agent VM Observability</title>
  <style>
    body {{ margin: 0; font: 14px/1.4 -apple-system, BlinkMacSystemFont, sans-serif; background: #f7f7f5; color: #161616; }}
    header {{ padding: 28px 32px; background: #151a1f; color: white; }}
    main {{ padding: 24px 32px 48px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    section {{ background: white; border: 1px solid #d8d8d2; border-radius: 8px; padding: 14px; display: flex; justify-content: space-between; }}
    table {{ width: 100%; border-collapse: collapse; background: white; margin-bottom: 28px; }}
    th, td {{ border-bottom: 1px solid #e3e3dd; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #ecece6; font-weight: 650; }}
    pre {{ background: #151a1f; color: #eef2f1; padding: 16px; border-radius: 8px; overflow: auto; }}
  </style>
</head>
<body>
  <header>
    <h1>Agent VM Observability</h1>
    <p>Local traces and shared memory across Codex and Claude Code.</p>
  </header>
  <main>
    <div class="cards">{count_cards}</div>
    <h2>Recent Events</h2>
    <table><thead><tr><th>Time</th><th>Agent</th><th>Project</th><th>Trace</th><th>Detail</th></tr></thead><tbody>{event_rows}</tbody></table>
    <h2>Recent Memories</h2>
    <table><thead><tr><th>Time</th><th>Project</th><th>Type</th><th>Title</th></tr></thead><tbody>{memory_rows}</tbody></table>
    <h2>Counts</h2>
    <pre>{counts_json}</pre>
  </main>
</body>
</html>"""

