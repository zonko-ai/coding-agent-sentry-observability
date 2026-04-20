from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import NormalizedTrace, infer_project
from .redaction import short_hash
from .timeutil import parse_timestamp, to_timestamp


SCHEMA = """
pragma journal_mode=wal;
pragma foreign_keys=on;

create table if not exists schema_versions (
  version integer primary key,
  applied_at text not null
);

create table if not exists agents (
  id integer primary key autoincrement,
  name text not null unique,
  created_at_epoch integer not null
);

create table if not exists agent_sessions (
  id integer primary key autoincrement,
  agent_id integer not null references agents(id),
  external_session_id text not null,
  project text,
  cwd text,
  repo text,
  git_branch text,
  started_at text,
  started_at_epoch integer,
  last_seen_at text,
  last_seen_at_epoch integer,
  status text not null default 'active',
  metadata_json text,
  unique(agent_id, external_session_id)
);

create table if not exists turns (
  id integer primary key autoincrement,
  session_db_id integer not null references agent_sessions(id) on delete cascade,
  external_turn_id text,
  prompt_number integer,
  prompt_text text,
  prompt_hash text,
  started_at text,
  started_at_epoch integer,
  metadata_json text,
  unique(session_db_id, external_turn_id)
);

create table if not exists events (
  id integer primary key autoincrement,
  agent_id integer not null references agents(id),
  session_db_id integer references agent_sessions(id) on delete set null,
  turn_db_id integer references turns(id) on delete set null,
  source_event_id text not null,
  kind text not null,
  title text not null,
  level text,
  timestamp text,
  timestamp_epoch integer,
  cwd text,
  project text,
  model text,
  tool_name text,
  success integer,
  duration_ms real,
  measurements_json text,
  tags_json text,
  extra_json text,
  content_hash text,
  created_at_epoch integer not null,
  unique(agent_id, source_event_id, kind)
);

create table if not exists tool_calls (
  id integer primary key autoincrement,
  event_id integer not null references events(id) on delete cascade,
  agent_id integer not null references agents(id),
  session_db_id integer references agent_sessions(id) on delete set null,
  tool_name text not null,
  tool_kind text,
  success integer,
  duration_ms real,
  metadata_json text,
  created_at_epoch integer not null
);

create table if not exists observations (
  id integer primary key autoincrement,
  source text not null,
  source_id text not null,
  agent text,
  source_session_id text,
  project text,
  type text,
  title text,
  subtitle text,
  facts text,
  narrative text,
  concepts text,
  files_read text,
  files_modified text,
  prompt_number integer,
  discovery_tokens integer default 0,
  created_at text,
  created_at_epoch integer,
  content_hash text,
  unique(source, source_id)
);

create table if not exists session_summaries (
  id integer primary key autoincrement,
  source text not null,
  source_id text not null,
  agent text,
  source_session_id text,
  project text,
  request text,
  investigated text,
  learned text,
  completed text,
  next_steps text,
  files_read text,
  files_edited text,
  notes text,
  prompt_number integer,
  discovery_tokens integer default 0,
  created_at text,
  created_at_epoch integer,
  unique(source, source_id)
);

create table if not exists memories (
  id integer primary key autoincrement,
  source text not null,
  source_id text not null,
  agent text,
  source_session_id text,
  project text,
  scope text not null default 'project',
  type text not null,
  title text,
  body text,
  confidence real default 0.75,
  status text not null default 'active',
  evidence_json text,
  created_at text,
  created_at_epoch integer,
  content_hash text,
  unique(source, source_id, type)
);

create table if not exists memory_sources (
  id integer primary key autoincrement,
  memory_id integer not null references memories(id) on delete cascade,
  source text not null,
  source_id text not null,
  source_table text,
  source_payload_json text
);

create index if not exists idx_events_agent_time on events(agent_id, timestamp_epoch desc);
create index if not exists idx_events_project_time on events(project, timestamp_epoch desc);
create index if not exists idx_sessions_project on agent_sessions(project);
create index if not exists idx_observations_project on observations(project);
create index if not exists idx_summaries_project on session_summaries(project);
create index if not exists idx_memories_project on memories(project, status);
create unique index if not exists idx_memory_sources_unique on memory_sources(memory_id, source, source_id, source_table);

create virtual table if not exists observations_fts using fts5(
  title, subtitle, facts, narrative, concepts,
  content='observations',
  content_rowid='id'
);

create virtual table if not exists session_summaries_fts using fts5(
  request, investigated, learned, completed, next_steps, notes,
  content='session_summaries',
  content_rowid='id'
);

create virtual table if not exists memories_fts using fts5(
  title, body, project, type,
  content='memories',
  content_rowid='id'
);
"""


@dataclass
class ImportResult:
    sessions: int = 0
    turns: int = 0
    observations: int = 0
    summaries: int = 0
    memories: int = 0


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialized = False

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys=on")
        conn.execute("pragma busy_timeout=30000")
        return conn

    def initialize(self) -> None:
        if self._initialized:
            return
        with closing(self.connect()) as conn:
            with conn:
                conn.executescript(SCHEMA)
                conn.execute(
                    "insert or ignore into schema_versions(version, applied_at) values (?, datetime('now'))",
                    (1,),
                )
        self._initialized = True

    def rebuild_fts(self, conn: sqlite3.Connection | None = None) -> None:
        own = conn is None
        if conn is None:
            conn = self.connect()
        try:
            conn.execute("insert into observations_fts(observations_fts) values ('rebuild')")
            conn.execute("insert into session_summaries_fts(session_summaries_fts) values ('rebuild')")
            conn.execute("insert into memories_fts(memories_fts) values ('rebuild')")
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def agent_id(self, conn: sqlite3.Connection, name: str) -> int:
        now = int(time.time())
        conn.execute("insert or ignore into agents(name, created_at_epoch) values (?, ?)", (name, now))
        row = conn.execute("select id from agents where name = ?", (name,)).fetchone()
        return int(row["id"])

    def record_trace(self, trace: NormalizedTrace) -> None:
        self.initialize()
        with closing(self.connect()) as conn:
            with conn:
                agent_id = self.agent_id(conn, trace.agent)
                session_db_id = None
                if trace.session_id:
                    session_db_id = self._upsert_session(conn, agent_id, trace)
                turn_db_id = None
                if session_db_id and trace.turn_id:
                    turn_db_id = self._upsert_turn(conn, session_db_id, trace)
                event_id = self._upsert_event(conn, agent_id, session_db_id, turn_db_id, trace)
                if trace.tool_name and event_id:
                    conn.execute(
                        """
                        insert into tool_calls(event_id, agent_id, session_db_id, tool_name, tool_kind, success, duration_ms, metadata_json, created_at_epoch)
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            agent_id,
                            session_db_id,
                            trace.tool_name,
                            trace.tool_kind,
                            _bool_int(trace.success),
                            trace.duration_ms,
                            json.dumps(trace.extra, sort_keys=True, default=str),
                            int(time.time()),
                        ),
                    )

    def _upsert_session(self, conn: sqlite3.Connection, agent_id: int, trace: NormalizedTrace) -> int:
        now = int(time.time())
        ts = to_timestamp(trace.timestamp)
        metadata = {"provider": trace.provider, "agent_version": trace.agent_version, "model": trace.model}
        conn.execute(
            """
            insert into agent_sessions(agent_id, external_session_id, project, cwd, repo, git_branch, started_at, started_at_epoch, last_seen_at, last_seen_at_epoch, metadata_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(agent_id, external_session_id) do update set
              project=coalesce(excluded.project, agent_sessions.project),
              cwd=coalesce(excluded.cwd, agent_sessions.cwd),
              repo=coalesce(excluded.repo, agent_sessions.repo),
              git_branch=coalesce(excluded.git_branch, agent_sessions.git_branch),
              last_seen_at=excluded.last_seen_at,
              last_seen_at_epoch=excluded.last_seen_at_epoch
            """,
            (
                agent_id,
                trace.session_id,
                trace.project,
                trace.cwd,
                trace.repo,
                trace.git_branch,
                ts,
                int(trace.timestamp.timestamp()) if trace.timestamp else now,
                ts,
                int(trace.timestamp.timestamp()) if trace.timestamp else now,
                json.dumps(metadata, sort_keys=True),
            ),
        )
        row = conn.execute(
            "select id from agent_sessions where agent_id = ? and external_session_id = ?",
            (agent_id, trace.session_id),
        ).fetchone()
        return int(row["id"])

    def _upsert_turn(self, conn: sqlite3.Connection, session_db_id: int, trace: NormalizedTrace) -> int:
        now = int(time.time())
        ts = to_timestamp(trace.timestamp)
        conn.execute(
            """
            insert into turns(session_db_id, external_turn_id, started_at, started_at_epoch, metadata_json)
            values (?, ?, ?, ?, ?)
            on conflict(session_db_id, external_turn_id) do update set metadata_json=excluded.metadata_json
            """,
            (
                session_db_id,
                trace.turn_id,
                ts,
                int(trace.timestamp.timestamp()) if trace.timestamp else now,
                json.dumps({"trace_id": trace.trace_id, "span_id": trace.span_id}, sort_keys=True),
            ),
        )
        row = conn.execute(
            "select id from turns where session_db_id = ? and external_turn_id = ?",
            (session_db_id, trace.turn_id),
        ).fetchone()
        return int(row["id"])

    def _upsert_event(
        self,
        conn: sqlite3.Connection,
        agent_id: int,
        session_db_id: int | None,
        turn_db_id: int | None,
        trace: NormalizedTrace,
    ) -> int | None:
        now = int(time.time())
        source_event_id = trace.stable_event_id()
        measurements = trace.all_measurements()
        content_hash = short_hash(json.dumps({"tags": trace.tags, "extra": trace.extra}, sort_keys=True, default=str))
        conn.execute(
            """
            insert or ignore into events(
              agent_id, session_db_id, turn_db_id, source_event_id, kind, title, level, timestamp, timestamp_epoch,
              cwd, project, model, tool_name, success, duration_ms, measurements_json, tags_json, extra_json,
              content_hash, created_at_epoch
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                session_db_id,
                turn_db_id,
                source_event_id,
                trace.kind,
                trace.title,
                trace.level,
                to_timestamp(trace.timestamp),
                int(trace.timestamp.timestamp()) if trace.timestamp else now,
                trace.cwd,
                trace.project,
                trace.model,
                trace.tool_name,
                _bool_int(trace.success),
                trace.duration_ms,
                json.dumps(measurements, sort_keys=True),
                json.dumps(trace.tags, sort_keys=True, default=str),
                json.dumps(trace.extra, sort_keys=True, default=str),
                content_hash,
                now,
            ),
        )
        row = conn.execute(
            "select id from events where agent_id = ? and source_event_id = ? and kind = ?",
            (agent_id, source_event_id, trace.kind),
        ).fetchone()
        return int(row["id"]) if row else None

    def import_claude_mem(self, source_db: Path) -> ImportResult:
        if not source_db.exists():
            raise FileNotFoundError(source_db)
        self.initialize()
        result = ImportResult()
        source_uri = f"file:{source_db}?mode=ro&cache=shared"
        src = sqlite3.connect(source_uri, uri=True)
        src.row_factory = sqlite3.Row
        try:
            with closing(self.connect()) as dst:
                with dst:
                    agent_id = self.agent_id(dst, "claude-code")
                    session_map: dict[str, int] = {}
                    for row in src.execute("select * from sdk_sessions"):
                        project = row["project"]
                        content_session_id = row["content_session_id"]
                        dst.execute(
                            """
                            insert into agent_sessions(agent_id, external_session_id, project, started_at, started_at_epoch,
                              last_seen_at, last_seen_at_epoch, status, metadata_json)
                            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            on conflict(agent_id, external_session_id) do update set
                              project=excluded.project,
                              last_seen_at=coalesce(excluded.last_seen_at, agent_sessions.last_seen_at),
                              last_seen_at_epoch=coalesce(excluded.last_seen_at_epoch, agent_sessions.last_seen_at_epoch),
                              metadata_json=excluded.metadata_json
                            """,
                            (
                                agent_id,
                                content_session_id,
                                project,
                                row["started_at"],
                                row["started_at_epoch"],
                                row["completed_at"],
                                row["completed_at_epoch"],
                                row["status"],
                                json.dumps(_row_payload(row), sort_keys=True, default=str),
                            ),
                        )
                        session_id = int(
                            dst.execute(
                                "select id from agent_sessions where agent_id=? and external_session_id=?",
                                (agent_id, content_session_id),
                            ).fetchone()["id"]
                        )
                        session_map[content_session_id] = session_id
                        result.sessions += 1

                    for row in src.execute("select * from user_prompts"):
                        session_db_id = session_map.get(row["content_session_id"])
                        if not session_db_id:
                            continue
                        external_turn_id = f"claude-mem-prompt-{row['id']}"
                        prompt_text = row["prompt_text"] or ""
                        dst.execute(
                            """
                            insert or ignore into turns(session_db_id, external_turn_id, prompt_number, prompt_text, prompt_hash,
                              started_at, started_at_epoch, metadata_json)
                            values (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                session_db_id,
                                external_turn_id,
                                row["prompt_number"],
                                prompt_text,
                                short_hash(prompt_text),
                                row["created_at"],
                                row["created_at_epoch"],
                                json.dumps({"source": "claude-mem", "source_id": row["id"]}, sort_keys=True),
                            ),
                        )
                        result.turns += 1

                    memory_session_map = {
                        row["memory_session_id"]: row["content_session_id"]
                        for row in src.execute("select memory_session_id, content_session_id from sdk_sessions where memory_session_id is not null")
                    }
                    memory_ids: list[int] = []
                    for row in src.execute("select * from observations"):
                        source_id = str(row["id"])
                        source_session = memory_session_map.get(row["memory_session_id"])
                        body = _join_text(row, ["narrative", "facts", "concepts", "text"])
                        dst.execute(
                            """
                            insert or ignore into observations(source, source_id, agent, source_session_id, project, type, title, subtitle,
                              facts, narrative, concepts, files_read, files_modified, prompt_number, discovery_tokens,
                              created_at, created_at_epoch, content_hash)
                            values ('claude-mem', ?, 'claude-code', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                source_session,
                                row["project"],
                                row["type"],
                                row["title"],
                                row["subtitle"],
                                row["facts"],
                                row["narrative"],
                                row["concepts"],
                                row["files_read"],
                                row["files_modified"],
                                row["prompt_number"],
                                row["discovery_tokens"],
                                row["created_at"],
                                row["created_at_epoch"],
                                row["content_hash"] or short_hash(body),
                            ),
                        )
                        memory_id = self._insert_memory_from_source(
                            dst,
                            source_id=source_id,
                            source_table="observations",
                            agent="claude-code",
                            source_session_id=source_session,
                            project=row["project"],
                            mem_type=row["type"] or "observation",
                            title=row["title"],
                            body=body,
                            created_at=row["created_at"],
                            created_at_epoch=row["created_at_epoch"],
                            payload=_row_payload(row),
                        )
                        if memory_id:
                            memory_ids.append(memory_id)
                        result.observations += 1

                    for row in src.execute("select * from session_summaries"):
                        source_id = str(row["id"])
                        source_session = memory_session_map.get(row["memory_session_id"])
                        body = _join_text(row, ["request", "investigated", "learned", "completed", "next_steps", "notes"])
                        dst.execute(
                            """
                            insert or ignore into session_summaries(source, source_id, agent, source_session_id, project, request,
                              investigated, learned, completed, next_steps, files_read, files_edited, notes, prompt_number,
                              discovery_tokens, created_at, created_at_epoch)
                            values ('claude-mem', ?, 'claude-code', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                source_session,
                                row["project"],
                                row["request"],
                                row["investigated"],
                                row["learned"],
                                row["completed"],
                                row["next_steps"],
                                row["files_read"],
                                row["files_edited"],
                                row["notes"],
                                row["prompt_number"],
                                row["discovery_tokens"],
                                row["created_at"],
                                row["created_at_epoch"],
                            ),
                        )
                        memory_id = self._insert_memory_from_source(
                            dst,
                            source_id=source_id,
                            source_table="session_summaries",
                            agent="claude-code",
                            source_session_id=source_session,
                            project=row["project"],
                            mem_type="session_summary",
                            title=row["request"] or f"Session summary {source_id}",
                            body=body,
                            created_at=row["created_at"],
                            created_at_epoch=row["created_at_epoch"],
                            payload=_row_payload(row),
                        )
                        if memory_id:
                            memory_ids.append(memory_id)
                        result.summaries += 1

                    result.memories = len(memory_ids)
                    self.rebuild_fts(dst)
        finally:
            src.close()
        return result

    def _insert_memory_from_source(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        source_table: str,
        agent: str,
        source_session_id: str | None,
        project: str | None,
        mem_type: str,
        title: str | None,
        body: str,
        created_at: str | None,
        created_at_epoch: int | None,
        payload: dict[str, Any],
    ) -> int | None:
        if not body.strip() and not (title or "").strip():
            return None
        content_hash = short_hash(f"{title or ''}\n{body}")
        conn.execute(
            """
            insert or ignore into memories(source, source_id, agent, source_session_id, project, scope, type, title, body,
              confidence, status, evidence_json, created_at, created_at_epoch, content_hash)
            values ('claude-mem', ?, ?, ?, ?, 'project', ?, ?, ?, 0.8, 'active', ?, ?, ?, ?)
            """,
            (
                source_id,
                agent,
                source_session_id,
                project,
                mem_type,
                title,
                body,
                json.dumps({"source_table": source_table, "source_id": source_id}, sort_keys=True),
                created_at,
                created_at_epoch,
                content_hash,
            ),
        )
        row = conn.execute(
            "select id from memories where source='claude-mem' and source_id=? and type=?",
            (source_id, mem_type),
        ).fetchone()
        if not row:
            return None
        memory_id = int(row["id"])
        conn.execute(
            """
            insert or ignore into memory_sources(memory_id, source, source_id, source_table, source_payload_json)
            values (?, 'claude-mem', ?, ?, ?)
            """,
            (memory_id, source_id, source_table, json.dumps(payload, sort_keys=True, default=str)),
        )
        return memory_id

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self.connect()) as conn:
            try:
                rows = conn.execute(
                    """
                    select 'memory' as source, m.id, m.project, m.type, m.title, snippet(memories_fts, 1, '[', ']', '...', 24) as body
                    from memories_fts f join memories m on m.id = f.rowid
                    where memories_fts match ?
                    union all
                    select 'observation', o.id, o.project, o.type, o.title, snippet(observations_fts, 3, '[', ']', '...', 24)
                    from observations_fts f join observations o on o.id = f.rowid
                    where observations_fts match ?
                    union all
                    select 'session_summary', s.id, s.project, 'session_summary', s.request, snippet(session_summaries_fts, 2, '[', ']', '...', 24)
                    from session_summaries_fts f join session_summaries s on s.id = f.rowid
                    where session_summaries_fts match ?
                    limit ?
                    """,
                    (query, query, query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                rows = conn.execute(
                    """
                    select 'memory' as source, id, project, type, title, substr(body, 1, 240) as body
                    from memories
                    where title like ? or body like ?
                    limit ?
                    """,
                    (like, like, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def context(self, cwd: str, agent: str, limit: int = 12) -> str:
        self.initialize()
        project = infer_project(cwd) or Path(cwd).name
        with closing(self.connect()) as conn:
            memories = conn.execute(
                """
                select agent, type, title, body, created_at
                from memories
                where status='active' and (project = ? or scope = 'global')
                order by created_at_epoch desc
                limit ?
                """,
                (project, limit),
            ).fetchall()
            summaries = conn.execute(
                """
                select request, learned, completed, next_steps, created_at
                from session_summaries
                where project = ?
                order by created_at_epoch desc
                limit 5
                """,
                (project,),
            ).fetchall()

        lines = [f"# Agent VM Context", "", f"- target_agent: {agent}", f"- cwd: {cwd}", f"- project: {project}", ""]
        lines.append("## Active Memories")
        if not memories:
            lines.append("- No active memories found for this project.")
        for row in memories:
            title = row["title"] or row["type"]
            body = (row["body"] or "").replace("\n", " ")
            lines.append(f"- [{row['agent'] or 'unknown'}:{row['type']}] {title}: {body[:500]}")
        lines.append("")
        lines.append("## Recent Session Summaries")
        if not summaries:
            lines.append("- No session summaries found for this project.")
        for row in summaries:
            request = (row["request"] or "Session").replace("\n", " ")
            learned = (row["learned"] or row["completed"] or row["next_steps"] or "").replace("\n", " ")
            lines.append(f"- {request}: {learned[:500]}")
        return "\n".join(lines)

    def summarize_session(self, external_session_id: str) -> str:
        self.initialize()
        with closing(self.connect()) as conn:
            session = conn.execute(
                """
                select s.*, a.name as agent
                from agent_sessions s join agents a on a.id=s.agent_id
                where s.external_session_id = ?
                order by s.last_seen_at_epoch desc
                limit 1
                """,
                (external_session_id,),
            ).fetchone()
            if not session:
                return f"No session found for {external_session_id}"
            events = conn.execute(
                """
                select kind, title, model, tool_name, success, duration_ms, timestamp
                from events
                where session_db_id = ?
                order by timestamp_epoch asc
                limit 80
                """,
                (session["id"],),
            ).fetchall()
        lines = [
            "# Session Summary",
            "",
            f"- agent: {session['agent']}",
            f"- session: {external_session_id}",
            f"- project: {session['project']}",
            f"- cwd: {session['cwd']}",
            f"- events: {len(events)}",
            "",
            "## Events",
        ]
        for row in events:
            detail = row["tool_name"] or row["model"] or ""
            lines.append(f"- {row['timestamp']}: {row['title']} {detail}".rstrip())
        return "\n".join(lines)

    def counts(self) -> dict[str, int]:
        self.initialize()
        with closing(self.connect()) as conn:
            names = ["agent_sessions", "turns", "events", "tool_calls", "observations", "session_summaries", "memories"]
            return {name: int(conn.execute(f"select count(*) as n from {name}").fetchone()["n"]) for name in names}


def _bool_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _join_text(row: sqlite3.Row, keys: Iterable[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = row[key] if key in row.keys() else None
        if value:
            parts.append(str(value))
    return "\n\n".join(parts)
