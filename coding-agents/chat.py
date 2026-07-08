#!/usr/bin/env python3
"""Minimal chat CLI for the coding-agent service.

Starts a fresh session bound to the active workspace and loops user/agent
exchanges, streaming tool activity as it happens.

Usage:
    uv run python chat.py            # needs the server running (./startup.sh)

Commands inside the chat:
    /end       close the session and exit
    /listmcp   list active MCP servers and their tools

Env overrides: API_URL (default http://localhost:8000), API_KEY (demo key).
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from rich.console import Console
from rich.markdown import Markdown

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "demo-key-acme")

HEADERS = {"Authorization": f"Bearer {API_KEY}"}
console = Console()


def dim(text: str) -> str:  # kept for input() prompts, which bypass rich
    return f"\033[2m{text}\033[0m" if sys.stdout.isatty() else text


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def die(msg: str) -> None:
    console.print(f"error: {msg}", style="red")
    sys.exit(1)


def create_session(client: httpx.Client) -> dict:
    r = client.post(f"{API_URL}/v1/sessions", headers=HEADERS, json={})
    if r.status_code == 503:
        die("sessions need Postgres — start the server with ./startup.sh")
    r.raise_for_status()
    return r.json()


def close_session(client: httpx.Client, session_id: str) -> None:
    try:
        client.post(f"{API_URL}/v1/sessions/{session_id}/close", headers=HEADERS)
    except httpx.HTTPError:
        pass  # best effort — the session just stays open


def list_mcp(client: httpx.Client) -> None:
    console.print("querying MCP servers (launches them if not already running)...", style="dim")
    r = client.get(f"{API_URL}/v1/mcp", headers=HEADERS, timeout=240)
    r.raise_for_status()
    data = r.json()
    if not data["servers"]:
        console.print("no MCP servers registered")
        return
    for srv in data["servers"]:
        console.print(f"[bold]{srv['name']}[/bold] ({len(srv['tools'])} tools)")
        for tool in srv["tools"]:
            console.print(f"  - {tool}", highlight=False)


def send_message(client: httpx.Client, session_id: str, prompt: str) -> None:
    url = f"{API_URL}/v1/sessions/{session_id}/messages/stream"
    event = None
    with client.stream(
        "POST", url, headers=HEADERS, json={"prompt": prompt},
        timeout=httpx.Timeout(10, read=None),
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
                continue
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: "):])

            if event == "workspace_instructions" and payload.get("loaded"):
                console.print(f"· AGENTS.md ({payload.get('mode', '?')})", style="dim", highlight=False)
            elif event == "run_item":
                item = payload.get("item")
                if item == "tool_call_item":
                    args = (payload.get("arguments") or "")[:120]
                    console.print(f"· {payload.get('tool')} {args}", style="dim", highlight=False)
                elif item == "tool_call_output_item":
                    out = (payload.get("output") or "").replace("\n", " ")[:120]
                    console.print(f"  -> {out}", style="dim", highlight=False)
            elif event == "done":
                console.print()
                console.print("[bold cyan]agent>[/bold cyan]")
                console.print(Markdown(payload.get("output", "")))
                console.print()
            elif event == "error":
                console.print(f"error: {payload.get('error')}: {payload.get('detail')}", style="red")


def main() -> None:
    with httpx.Client(timeout=30) as client:
        try:
            client.get(f"{API_URL}/healthz").raise_for_status()
        except httpx.HTTPError:
            die(f"cannot reach the service at {API_URL} — is it running?")

        session = create_session(client)
        sid = session["session_id"]
        ws = session.get("workspace_path") or "(tenant default)"
        console.print(f"session [bold]{sid[:8]}[/bold] | workspace: {ws}", highlight=False)
        console.print("type a message; /end to quit, /listmcp for MCP servers\n", style="dim")

        try:
            while True:
                try:
                    user = input(f"{bold('you>')} ").strip()
                except EOFError:
                    print()
                    break
                if not user:
                    continue
                if user == "/end":
                    break
                if user == "/listmcp":
                    list_mcp(client)
                    continue
                try:
                    send_message(client, sid, user)
                except httpx.HTTPError as exc:
                    console.print(f"request failed: {exc}", style="red")
        except KeyboardInterrupt:
            print()
        finally:
            close_session(client, sid)
            console.print(f"session {sid[:8]} closed", style="dim")


if __name__ == "__main__":
    main()
