import sqlite3
from pathlib import Path

from agent_vm_observability.memory import MemoryStore


def create_claude_mem_fixture(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table sdk_sessions (
          id integer primary key autoincrement,
          content_session_id text unique not null,
          memory_session_id text unique,
          project text not null,
          user_prompt text,
          started_at text not null,
          started_at_epoch integer not null,
          completed_at text,
          completed_at_epoch integer,
          status text not null default 'active',
          worker_port integer,
          prompt_counter integer default 0,
          custom_title text
        );
        create table user_prompts (
          id integer primary key autoincrement,
          content_session_id text not null,
          prompt_number integer not null,
          prompt_text text not null,
          created_at text not null,
          created_at_epoch integer not null
        );
        create table observations (
          id integer primary key autoincrement,
          memory_session_id text not null,
          project text not null,
          text text,
          type text not null,
          title text,
          subtitle text,
          facts text,
          narrative text,
          concepts text,
          files_read text,
          files_modified text,
          prompt_number integer,
          discovery_tokens integer default 0,
          created_at text not null,
          created_at_epoch integer not null,
          content_hash text
        );
        create table session_summaries (
          id integer primary key autoincrement,
          memory_session_id text not null,
          project text not null,
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
          created_at text not null,
          created_at_epoch integer not null
        );
        """
    )
    conn.execute(
        "insert into sdk_sessions(content_session_id, memory_session_id, project, started_at, started_at_epoch, status) values (?, ?, ?, ?, ?, ?)",
        ("claude-session-1", "mem-session-1", "example", "2026-04-20T11:00:00Z", 1776682800, "active"),
    )
    conn.execute(
        "insert into user_prompts(content_session_id, prompt_number, prompt_text, created_at, created_at_epoch) values (?, ?, ?, ?, ?)",
        ("claude-session-1", 1, "remember dashboard decision", "2026-04-20T11:01:00Z", 1776682860),
    )
    conn.execute(
        "insert into observations(memory_session_id, project, type, title, narrative, created_at, created_at_epoch) values (?, ?, ?, ?, ?, ?, ?)",
        ("mem-session-1", "example", "decision", "Dashboard decision", "Use Sentry plus local SQLite dashboard.", "2026-04-20T11:02:00Z", 1776682920),
    )
    conn.execute(
        "insert into session_summaries(memory_session_id, project, request, learned, created_at, created_at_epoch) values (?, ?, ?, ?, ?, ?)",
        ("mem-session-1", "example", "Build observability", "Central memory should import claude-mem.", "2026-04-20T11:03:00Z", 1776682980),
    )
    conn.commit()
    conn.close()


def test_import_claude_mem_and_search(tmp_path: Path) -> None:
    source = tmp_path / "claude-mem.db"
    create_claude_mem_fixture(source)
    store = MemoryStore(tmp_path / "memory.db")
    result = store.import_claude_mem(source)
    assert result.sessions == 1
    assert result.turns == 1
    assert result.observations == 1
    assert result.summaries == 1
    rows = store.search("dashboard", limit=5)
    assert rows
    context = store.context("/Users/example/3_zonko_projects/example", "codex")
    assert "Dashboard decision" in context

