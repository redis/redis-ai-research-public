"""Small HTTP API for enqueueing cohort requests."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from redis import Redis
from rich.console import Console

from service.cohort_job import generate_job_id, list_jobs, normalize_session_ids, record_job_status
from service.config import PROJECT_ROOT, load_config


CONSOLE = Console()


def _build_parser() -> argparse.ArgumentParser:
    """Create the HTTP API CLI parser."""
    parser = argparse.ArgumentParser(description="HTTP API for enqueueing cohort jobs.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port.")
    parser.add_argument("--redis-url", default=None, help="Redis URL. Defaults to REDIS_URL.")
    parser.add_argument("--stream", default=None, help="Redis Stream name.")
    return parser


class CohortRequestHandler(BaseHTTPRequestHandler):
    """Handle simple JSON cohort enqueue requests."""

    redis_url: str
    stream_name: str

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP logs through Rich console."""
        CONSOLE.print(f"[dim]{self.address_string()} - {format % args}[/dim]")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        response = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _redis_client(self) -> Redis:
        return Redis.from_url(self.redis_url)

    def do_GET(self) -> None:
        """Return health, job status, file lists, or dashboard HTML."""
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed_url.path == "/jobs":
            self._send_json(HTTPStatus.OK, {"jobs": list_jobs(self._redis_client())})
            return
        if parsed_url.path == "/files":
            self._send_json(HTTPStatus.OK, {"files": _list_cohort_analysis_files()})
            return
        if parsed_url.path == "/file":
            query = parse_qs(parsed_url.query)
            requested_path = query.get("path", [""])[0]
            try:
                self._send_file(requested_path)
            except (FileNotFoundError, ValueError) as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        if parsed_url.path == "/":
            self._send_html(HTTPStatus.OK, _render_dashboard(self._redis_client()))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _send_file(self, requested_path: str) -> None:
        """Serve one safe file from cohort-analysis."""
        file_path = _resolve_cohort_analysis_file(requested_path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "text/plain"
        if content_type.startswith("text/") or file_path.suffix in {".json", ".log"}:
            self._send_html(HTTPStatus.OK, _render_file_content(requested_path, file_path))
            return

        payload = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        """Accept cohort JSON payloads and dashboard note updates."""
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/notes":
            self._save_notes()
            return
        if parsed_url.path != "/cohorts":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            cohort_name = str(payload.get("cohort_name", "")).strip()
            if not cohort_name:
                raise ValueError('Field "cohort_name" is required.')
            session_ids = normalize_session_ids(payload.get("session_ids", []))
            redis_client = self._redis_client()
            job_id = generate_job_id()
            record_job_status(
                redis_client,
                job_id,
                status="submitted",
                cohort_name=cohort_name,
                session_ids=session_ids,
                session_count=len(session_ids),
            )
            message_id = redis_client.xadd(
                self.stream_name,
                {
                    "job_id": job_id,
                    "cohort_name": cohort_name,
                    "session_ids": json.dumps(session_ids),
                },
            )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self._send_json(
            HTTPStatus.ACCEPTED,
            {
                "job_id": job_id,
                "message_id": message_id.decode(),
                "cohort_name": cohort_name,
                "session_count": len(session_ids),
            },
        )

    def _save_notes(self) -> None:
        """Persist per-file dashboard notes from a form post."""
        content_length = int(self.headers.get("Content-Length", "0"))
        form_body = self.rfile.read(content_length).decode("utf-8")
        form_values = parse_qs(form_body)
        notes = _read_dashboard_notes()
        for key, values in form_values.items():
            if not key.startswith("note:") or not values:
                continue
            file_path = key.removeprefix("note:")
            note = values[0].strip()
            if note:
                notes[file_path] = note
            else:
                notes.pop(file_path, None)
        _write_dashboard_notes(notes)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()


def _list_cohort_analysis_files(limit: int = 250) -> list[dict[str, Any]]:
    """List recent files written under cohort-analysis, excluding metric artifacts."""
    root = PROJECT_ROOT / "cohort-analysis"
    if not root.exists():
        return []

    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(PROJECT_ROOT)
        if relative_path.parts[:2] == ("cohort-analysis", "metrics"):
            continue
        if relative_path.name in {"worker-notes.json", "worker-notes.txt"}:
            continue
        stat = path.stat()
        files.append(
            {
                "path": str(relative_path),
                "url": f"/file?path={quote(str(relative_path))}",
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return files[:limit]


def _html_escape(value: Any) -> str:
    """Escape text for simple HTML rendering."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _dashboard_notes_path() -> Path:
    """Return the local dashboard notes file path."""
    return PROJECT_ROOT / "cohort-analysis" / "worker-notes.json"


def _read_dashboard_notes() -> dict[str, str]:
    """Read per-file dashboard notes if present."""
    notes_path = _dashboard_notes_path()
    if not notes_path.is_file():
        return {}
    try:
        notes = json.loads(notes_path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(notes, dict):
        return {}
    return {str(key): str(value) for key, value in notes.items()}


def _write_dashboard_notes(notes: dict[str, str]) -> None:
    """Write per-file dashboard notes."""
    notes_path = _dashboard_notes_path()
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(json.dumps(notes, indent=2, sort_keys=True))


def _resolve_cohort_analysis_file(requested_path: str) -> Path:
    """Resolve and validate a cohort-analysis file path from a query string."""
    if not requested_path:
        raise ValueError("Missing file path.")
    relative_path = Path(requested_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Invalid file path.")
    if not relative_path.parts or relative_path.parts[0] != "cohort-analysis":
        raise ValueError("File must be under cohort-analysis/.")
    if len(relative_path.parts) > 1 and relative_path.parts[1] == "metrics":
        raise ValueError("Metric artifact folders are not browsable here.")

    file_path = (PROJECT_ROOT / relative_path).resolve()
    cohort_root = (PROJECT_ROOT / "cohort-analysis").resolve()
    if cohort_root not in file_path.parents and file_path != cohort_root:
        raise ValueError("Invalid file path.")
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {requested_path}")
    return file_path


def _theme_assets() -> dict[str, str]:
    """Return shared assets for light/dark theme toggling."""
    css = """
    @font-face {
      font-family: arizonaSans;
      src: url('https://www.symbolica.ai/_next/static/media/451abb0938f7ae3a-s.p.woff2') format('woff2');
      font-display: swap;
      font-weight: 300;
      font-style: normal;
    }
    @font-face {
      font-family: arizonaSans;
      src: url('https://www.symbolica.ai/_next/static/media/385b087e8876245d-s.p.woff2') format('woff2');
      font-display: swap;
      font-weight: 400;
      font-style: normal;
    }
    @font-face {
      font-family: arizonaSans;
      src: url('https://www.symbolica.ai/_next/static/media/c2311a12fdaa915f-s.p.woff') format('woff');
      font-display: swap;
      font-weight: 600;
      font-style: normal;
    }
    @font-face {
      font-family: 'IBM Plex Mono';
      src: url('https://www.symbolica.ai/_next/static/media/d3ebbfd689654d3a-s.p.woff2') format('woff2');
      font-display: swap;
      font-weight: 400;
      font-style: normal;
    }
    @font-face {
      font-family: 'IBM Plex Mono';
      src: url('https://www.symbolica.ai/_next/static/media/98e207f02528a563-s.p.woff2') format('woff2');
      font-display: swap;
      font-weight: 500;
      font-style: normal;
    }
    .topbar { align-items: flex-start; display: flex; justify-content: space-between; gap: 16px; }
    .theme-toggle {
      background: #1a1a1e;
      border: 1px solid #28292c;
      border-radius: 999px;
      color: #f6f6fa;
      cursor: pointer;
      font-weight: 700;
      margin-top: 0;
      padding: 7px 12px;
      white-space: nowrap;
    }
    .theme-toggle:hover { border-color: #7678ed; color: #9ea0ff; }
    .tool-params { display: none; color: #8b8b90; font-style: italic; }
    body.show-tool-params .tool-params { display: inline; }
    .param-toggle {
      background: #1a1a1e;
      border: 1px solid #28292c;
      border-radius: 8px;
      color: #f6f6fa;
      cursor: pointer;
      font-weight: 700;
      margin: 8px 0 12px;
      padding: 7px 12px;
    }
    .param-toggle:hover { border-color: #7678ed; color: #9ea0ff; }
    html[data-theme="light"] { color-scheme: light; }
    html[data-theme="light"] body { background: #ffffff; color: #535357; }
    html[data-theme="light"] h1,
    html[data-theme="light"] h2 { color: #1a1a1e; }
    html[data-theme="light"] p,
    html[data-theme="light"] .subtitle { color: #737377; }
    html[data-theme="light"] a { color: #5b5ee8; }
    html[data-theme="light"] table,
    html[data-theme="light"] .log-section { background: #ffffff; border-color: #e2e2e5; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08); }
    html[data-theme="light"] th,
    html[data-theme="light"] .log-heading { background: #f6f6fa; color: #1a1a1e; border-color: #e2e2e5; }
    html[data-theme="light"] td { border-color: #e2e2e5; }
    html[data-theme="light"] code,
    html[data-theme="light"] pre { background: #f6f6fa; border-color: #e2e2e5; color: #1a1a1e; }
    html[data-theme="light"] input[type="text"] { background: #ffffff; border-color: #d1d1d5; color: #1a1a1e; }
    html[data-theme="light"] input[type="text"]:focus { border-color: #7678ed; outline: 2px solid rgba(118, 120, 237, 0.2); }
    html[data-theme="light"] .theme-toggle { background: #f6f6fa; border-color: #d1d1d5; color: #1a1a1e; }
    html[data-theme="light"] .theme-toggle:hover { border-color: #7678ed; color: #5b5ee8; }
    html[data-theme="light"] .param-toggle { background: #f6f6fa; border-color: #d1d1d5; color: #1a1a1e; }
    html[data-theme="light"] .param-toggle:hover { border-color: #7678ed; color: #5b5ee8; }
    html[data-theme="light"] .tool-params { color: #737377; }
    html[data-theme="light"] .json-key { color: #4f46e5; }
    html[data-theme="light"] .json-string { color: #116329; }
    html[data-theme="light"] .json-number { color: #8250df; }
    html[data-theme="light"] .json-literal { color: #b45309; }
    html[data-theme="light"] .line-error { color: #cf222e; }
    html[data-theme="light"] .line-success { color: #116329; }
    html[data-theme="light"] .line-progress { color: #4f46e5; }
    html[data-theme="light"] .line-session { color: #7c3aed; }
    """
    script = """
  <script>
    (function () {
      const key = 'cohort-dashboard-theme';
      const savedTheme = localStorage.getItem(key) || 'dark';
      document.documentElement.setAttribute('data-theme', savedTheme);
      window.toggleTheme = function () {
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
        const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', nextTheme);
        localStorage.setItem(key, nextTheme);
      };
      window.toggleToolParams = function () {
        document.body.classList.toggle('show-tool-params');
        const button = document.getElementById('tool-param-toggle');
        if (button) {
          button.textContent = document.body.classList.contains('show-tool-params')
            ? 'Hide tool parameters'
            : 'Show tool parameters';
        }
      };
    })();
  </script>
    """
    button = '<button class="theme-toggle" type="button" onclick="toggleTheme()">Toggle theme</button>'
    return {"css": css, "script": script, "button": button}


def _render_file_content(requested_path: str, file_path: Path) -> str:
    """Render one text-like file as escaped HTML."""
    try:
        raw_content = file_path.read_text()
    except UnicodeDecodeError:
        raw_content = file_path.read_bytes().decode("utf-8", errors="replace")

    if file_path.suffix == ".json":
        return _render_json_content(requested_path, raw_content)
    if file_path.suffix == ".log":
        return _render_log_content(requested_path, raw_content)
    return _render_text_content(requested_path, raw_content)


def _render_file_page(requested_path: str, body_html: str, subtitle: str = "") -> str:
    """Render a shared file viewer page."""
    subtitle_html = f"<p class=\"subtitle\">{_html_escape(subtitle)}</p>" if subtitle else ""
    theme_assets = _theme_assets()
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{_html_escape(requested_path)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      background: #0b0b10;
      color: #d1d1d5;
      font-family: arizonaSans, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      margin: 0;
      padding: 32px;
    }}
    a {{ color: #7678ed; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1 {{ color: #f6f6fa; font-size: 28px; letter-spacing: -0.02em; margin: 16px 0 4px; }}
    .subtitle {{ color: #a2a2a5; margin-top: 0; }}
    pre {{
      background: #1a1a1e;
      border: 1px solid #28292c;
      border-radius: 10px;
      color: #e2e2e5;
      font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-variant-numeric: tabular-nums;
      line-height: 1.55;
      overflow-x: auto;
      padding: 18px;
      white-space: pre-wrap;
    }}
    .json-key {{ color: #9ea0ff; font-weight: 700; }}
    .json-string {{ color: #7ee787; }}
    .json-number {{ color: #d2a8ff; }}
    .json-literal {{ color: #ffa657; font-weight: 700; }}
    .log-section {{
      background: #111116;
      border: 1px solid #28292c;
      border-radius: 12px;
      margin: 18px 0;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
    }}
    .log-heading {{
      background: linear-gradient(90deg, rgba(118, 120, 237, 0.22), rgba(118, 120, 237, 0.04));
      border-bottom: 1px solid #28292c;
      color: #f6f6fa;
      font-weight: 800;
      padding: 10px 14px;
    }}
    .log-section pre {{ border: 0; border-radius: 0; margin: 0; white-space: pre; }}
    .svg-wrap {{
      background: #0b0b10;
      border-top: 1px solid #28292c;
      overflow-x: auto;
      padding: 18px;
    }}
    .svg-wrap svg {{ display: block; max-width: 100%; height: auto; }}
    .response-card {{ border-top: 1px solid #28292c; padding: 18px; }}
    .response-card:first-of-type {{ border-top: 0; }}
    .response-session {{ color: #d2a8ff; font-weight: 800; margin: 0 0 12px; }}
    .response-body {{ color: #d1d1d5; line-height: 1.6; }}
    .response-body p {{ margin: 0 0 12px; }}
    .response-body ul, .response-body ol {{ margin: 0 0 14px 22px; padding: 0; }}
    .response-body li {{ margin: 4px 0; }}
    .response-body pre {{ margin: 10px 0 14px; white-space: pre; }}
    .response-body code {{ font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    .line-error {{ color: #ff7b72; }}
    .line-success {{ color: #7ee787; }}
    .line-progress {{ color: #9ea0ff; }}
    .line-session {{ color: #d2a8ff; font-weight: 800; }}
    {theme_assets['css']}
  </style>
  {theme_assets['script']}
</head>
<body>
  <div class="topbar"><p><a href="/">Back to dashboard</a></p>{theme_assets['button']}</div>
  <h1>{_html_escape(requested_path)}</h1>
  {subtitle_html}
  {body_html}
</body>
</html>"""


def _render_text_content(requested_path: str, raw_content: str) -> str:
    """Render plain text content."""
    return _render_file_page(
        requested_path,
        f"<pre>{_html_escape(raw_content)}</pre>",
        subtitle="Text file",
    )


def _render_json_content(requested_path: str, raw_content: str) -> str:
    """Render JSON content with lightweight syntax highlighting."""
    try:
        pretty_content = json.dumps(json.loads(raw_content), indent=2)
    except json.JSONDecodeError:
        return _render_text_content(requested_path, raw_content)

    highlighted = _highlight_json(pretty_content)
    return _render_file_page(
        requested_path,
        f"<pre>{highlighted}</pre>",
        subtitle="Pretty-printed JSON",
    )


def _highlight_json(json_text: str) -> str:
    """Apply minimal JSON token highlighting to already-pretty JSON text."""
    token_pattern = re.compile(
        r'("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b'
    )

    def replace_token(match: re.Match[str]) -> str:
        string_token, key_suffix, number_token, literal_token = match.groups()
        if string_token is not None:
            escaped_string = _html_escape(string_token)
            if key_suffix:
                return f'<span class="json-key">{escaped_string}</span>{key_suffix}'
            return f'<span class="json-string">{escaped_string}</span>'
        if number_token is not None:
            return f'<span class="json-number">{_html_escape(number_token)}</span>'
        return f'<span class="json-literal">{_html_escape(literal_token)}</span>'

    highlighted_parts = []
    previous_end = 0
    for match in token_pattern.finditer(json_text):
        highlighted_parts.append(_html_escape(json_text[previous_end:match.start()]))
        highlighted_parts.append(replace_token(match))
        previous_end = match.end()
    highlighted_parts.append(_html_escape(json_text[previous_end:]))
    return "".join(highlighted_parts)


def _render_log_content(requested_path: str, raw_content: str) -> str:
    """Render worker log content in simple sections."""
    sections = _split_log_sections(raw_content)
    toggle_button = (
        '<button id="tool-param-toggle" class="param-toggle" type="button" '
        'onclick="toggleToolParams()">Show tool parameters</button>'
        if "__TOOL_PARAMS__" in raw_content
        else ""
    )
    sections_html = "".join(
        "<section class=\"log-section\">"
        f"<div class=\"log-heading\">{_html_escape(title)}</div>"
        f"{_render_final_output_responses(content) if title == 'FINAL_OUTPUT_RESPONSES' else _render_log_section_body(content)}"
        "</section>"
        for title, content in sections
    )
    return _render_file_page(requested_path, toggle_button + sections_html, subtitle="Worker log")


def _render_final_output_responses(content: str) -> str:
    """Render final output responses as session cards."""
    lines = content.splitlines()
    if lines and lines[0].strip() == "Final Output Responses":
        lines = lines[1:]

    cards = []
    current_session = None
    current_lines = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"ses_[A-Za-z0-9]+", stripped):
            if current_session is not None:
                cards.append((current_session, "\n".join(current_lines).strip()))
            current_session = stripped
            current_lines = []
            continue
        if current_session is not None:
            current_lines.append(line)
    if current_session is not None:
        cards.append((current_session, "\n".join(current_lines).strip()))

    if not cards:
        return f"<pre>{_highlight_log_lines(content)}</pre>"

    return "".join(
        "<article class=\"response-card\">"
        f"<h3 class=\"response-session\">{_html_escape(session_id)}</h3>"
        f"<div class=\"response-body\">{_render_response_markdown(response)}</div>"
        "</article>"
        for session_id, response in cards
    )


def _render_response_markdown(response: str) -> str:
    """Render a small markdown subset for final responses."""
    blocks = []
    paragraph_lines = []
    list_items = []
    ordered_items = []
    in_code_block = False
    code_lines = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            text = " ".join(line.strip() for line in paragraph_lines)
            blocks.append(f"<p>{_render_inline_markdown(text)}</p>")
            paragraph_lines = []

    def flush_lists() -> None:
        nonlocal list_items, ordered_items
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in list_items) + "</ul>")
            list_items = []
        if ordered_items:
            blocks.append("<ol>" + "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in ordered_items) + "</ol>")
            ordered_items = []

    for raw_line in response.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code_block:
                blocks.append(f"<pre>{_html_escape(chr(10).join(code_lines))}</pre>")
                code_lines = []
                in_code_block = False
            else:
                flush_paragraph()
                flush_lists()
                in_code_block = True
            continue
        if in_code_block:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_lists()
            continue
        bullet_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        ordered_match = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if bullet_match:
            flush_paragraph()
            ordered_items = []
            list_items.append(bullet_match.group(1))
            continue
        if ordered_match:
            flush_paragraph()
            list_items = []
            ordered_items.append(ordered_match.group(1))
            continue
        flush_lists()
        paragraph_lines.append(line)

    if in_code_block:
        blocks.append(f"<pre>{_html_escape(chr(10).join(code_lines))}</pre>")
    flush_paragraph()
    flush_lists()
    return "".join(blocks) or "<p><em>No final response captured.</em></p>"


def _render_inline_markdown(text: str) -> str:
    """Render minimal inline markdown safely."""
    escaped = _html_escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _render_log_section_body(content: str) -> str:
    """Render one log section, preserving text and rendering generated SVG blocks."""
    svg_pattern = re.compile(
        r"<!-- cohort-trajectory-svg-start -->(.*?)<!-- cohort-trajectory-svg-end -->",
        re.DOTALL,
    )
    parts = []
    previous_end = 0
    for match in svg_pattern.finditer(content):
        text_chunk = content[previous_end:match.start()].strip("\n")
        if text_chunk:
            parts.append(f"<pre>{_highlight_log_content(text_chunk)}</pre>")
        svg_content = match.group(1).strip()
        parts.append(f'<div class="svg-wrap">{svg_content}</div>')
        previous_end = match.end()
    trailing_chunk = content[previous_end:].strip("\n")
    if trailing_chunk:
        parts.append(f"<pre>{_highlight_log_content(trailing_chunk)}</pre>")
    return "".join(parts) or "<pre></pre>"


def _split_log_sections(raw_content: str) -> list[tuple[str, str]]:
    """Split worker logs into COMMAND, STDOUT, STDERR sections when present."""
    current_title = "LOG"
    current_lines = []
    sections = []
    headings = {"COMMAND:", "STDOUT:", "TRAJECTORY_TIMELINE_SVGS:", "FINAL_OUTPUT_RESPONSES:", "STDERR:"}
    for line in raw_content.splitlines():
        if line in headings:
            if current_lines or sections:
                sections.append((current_title, "\n".join(current_lines).strip("\n")))
            current_title = line.removesuffix(":")
            current_lines = []
            continue
        current_lines.append(line)
    sections.append((current_title, "\n".join(current_lines).strip("\n")))
    return [(title, content) for title, content in sections if content or title != "LOG"]


def _clean_tool_parameters(raw_parameters: str) -> str:
    """Normalize Rich-wrapped tool parameter marker content."""
    compact_parameters = re.sub(r"\n[\s│├└─]*", "", raw_parameters).strip()
    try:
        return json.dumps(json.loads(compact_parameters), separators=(",", ":"))
    except json.JSONDecodeError:
        return compact_parameters


def _highlight_log_content(content: str) -> str:
    """Highlight logs and hide tool parameter blocks by default."""
    marker_pattern = re.compile(
        r"(?m)^[^\n]*?__TOOL_PARAMS__(.*?)__END_TOOL_PARAMS__",
        re.DOTALL,
    )
    highlighted_parts = []
    previous_end = 0
    for match in marker_pattern.finditer(content):
        highlighted_parts.append(_highlight_log_lines(content[previous_end:match.start()]))
        parameters = _clean_tool_parameters(match.group(1))
        highlighted_parts.append(
            f'<span class="tool-params">  — parameters: {_html_escape(parameters)}</span>'
        )
        previous_end = match.end()
    highlighted_parts.append(_highlight_log_lines(content[previous_end:]))
    return "".join(highlighted_parts)


def _highlight_log_lines(content: str) -> str:
    """Highlight status/error lines in plain log text."""
    highlighted_lines = []
    for line in content.splitlines():
        lower_line = line.lower()
        css_class = ""
        error_match = re.search(r"\b(error|failed|traceback|exception)\b", lower_line)
        coverage_match = re.search(r"\s(\d+)\s+\d+(?:\.\d+)?%\s*$", line)
        session_match = re.search(r"(?:^|[├└│\s─]+)(ses_[A-Za-z0-9]+)\b", line)
        if session_match:
            css_class = "line-session"
        elif coverage_match and int(coverage_match.group(1)) > 0:
            css_class = "line-error"
        elif error_match or lower_line.strip().startswith("stderr"):
            css_class = "line-error"
        elif any(term in lower_line for term in ["succeeded", "completed", "wrote"]):
            css_class = "line-success"
        elif any(term in lower_line for term in ["processing", "queued", "running"]):
            css_class = "line-progress"
        escaped_line = _html_escape(line)
        if css_class:
            highlighted_lines.append(f'<span class="{css_class}">{escaped_line}</span>')
        else:
            highlighted_lines.append(escaped_line)
    return "\n".join(highlighted_lines)


def _render_dashboard(redis_client: Redis) -> str:
    """Render a lightweight HTML dashboard."""
    jobs = list_jobs(redis_client)
    files = _list_cohort_analysis_files(limit=100)
    notes = _read_dashboard_notes()
    theme_assets = _theme_assets()
    job_rows = "".join(
        "<tr>"
        f"<td>{_html_escape(job.get('updated_at', ''))}</td>"
        f"<td>{_html_escape(job.get('status', ''))}</td>"
        f"<td>{_html_escape(job.get('cohort_name', ''))}</td>"
        f"<td>{_html_escape(job.get('session_count', ''))}</td>"
        f"<td>{_html_escape(job.get('job_id', ''))}</td>"
        f"<td>{_html_escape(job.get('task_id', ''))}</td>"
        f"<td>{_html_escape(job.get('output_file', ''))}</td>"
        f"<td>{_html_escape(job.get('log_file', ''))}</td>"
        "</tr>"
        for job in jobs
    )
    file_rows = "".join(
        f"<tr class=\"{'has-note' if notes.get(file_info['path'], '').strip() else ''}\">"
        f"<td><a href=\"{_html_escape(file_info['url'])}\">{_html_escape(file_info['path'])}</a></td>"
        f"<td><input class=\"{'note-filled' if notes.get(file_info['path'], '').strip() else ''}\" type=\"text\" name=\"note:{_html_escape(file_info['path'])}\" value=\"{_html_escape(notes.get(file_info['path'], ''))}\" placeholder=\"Add note...\"></td>"
        "</tr>"
        for file_info in files
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cohort Worker Status</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      background: #0b0b10;
      color: #d1d1d5;
      font-family: arizonaSans, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      margin: 0;
      padding: 32px;
    }}
    h1 {{ color: #f6f6fa; font-size: 32px; letter-spacing: -0.03em; margin: 0 0 8px; }}
    h2 {{ color: #f6f6fa; letter-spacing: -0.02em; margin-top: 34px; }}
    p {{ color: #a2a2a5; }}
    a {{ color: #9ea0ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    table {{
      background: #111116;
      border: 1px solid #28292c;
      border-collapse: separate;
      border-radius: 12px;
      border-spacing: 0;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
      margin-bottom: 32px;
      overflow: hidden;
      width: 100%;
    }}
    th, td {{ border-bottom: 1px solid #28292c; padding: 9px 12px; font-size: 13px; vertical-align: top; }}
    tr:last-child td {{ border-bottom: 0; }}
    th {{ background: #1a1a1e; color: #f6f6fa; font-weight: 700; text-align: left; }}
    code {{ background: #1a1a1e; border: 1px solid #28292c; border-radius: 6px; color: #f6f6fa; padding: 2px 5px; }}
    input[type="text"] {{
      background: #0d0d11;
      border: 1px solid #424245;
      border-radius: 8px;
      box-sizing: border-box;
      color: #f6f6fa;
      font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      padding: 7px 9px;
      width: 100%;
    }}
    input[type="text"]:focus {{ border-color: #7678ed; outline: 2px solid rgba(118, 120, 237, 0.25); }}
    tr.has-note td {{ background: rgba(118, 120, 237, 0.10); }}
    input.note-filled {{
      background: rgba(118, 120, 237, 0.16);
      border-color: #7678ed;
      color: #ffffff;
    }}
    html[data-theme="light"] tr.has-note td {{ background: rgba(118, 120, 237, 0.10); }}
    html[data-theme="light"] input.note-filled {{
      background: #f1f1ff;
      border-color: #7678ed;
      color: #1a1a1e;
    }}
    button {{
      background: #7678ed;
      border: 0;
      border-radius: 8px;
      color: white;
      cursor: pointer;
      font-weight: 700;
      margin-top: 8px;
      padding: 8px 14px;
    }}
    button:hover {{ background: #8789ff; }}
    .note-hint {{ color: #a2a2a5; display: inline-block; font-size: 12px; margin-left: 10px; }}
    {theme_assets['css']}
  </style>
  {theme_assets['script']}
  <script>
    (function () {{
      let notesDirty = false;
      let notesFocused = false;
      window.addEventListener('DOMContentLoaded', function () {{
        document.querySelectorAll('input[name^="note:"]').forEach(function (input) {{
          input.addEventListener('input', function () {{ notesDirty = true; }});
          input.addEventListener('focus', function () {{ notesFocused = true; }});
          input.addEventListener('blur', function () {{ notesFocused = false; }});
        }});
        setInterval(function () {{
          if (!notesDirty && !notesFocused) {{ window.location.reload(); }}
        }}, 10000);
      }});
    }})();
  </script>
</head>
<body>
  <div class="topbar">
    <div>
      <h1>Cohort Worker Status</h1>
      <p>Auto-refreshes every 10 seconds unless you are editing notes. JSON endpoints: <code>/jobs</code>, <code>/files</code>, <code>/health</code>.</p>
    </div>
    {theme_assets['button']}
  </div>
  <h2>Jobs</h2>
  <table>
    <thead><tr><th>Updated</th><th>Status</th><th>Cohort</th><th>Sessions</th><th>Job ID</th><th>Task ID</th><th>Output</th><th>Log</th></tr></thead>
    <tbody>{job_rows}</tbody>
  </table>
  <h2>cohort-analysis Files</h2>
  <form method="post" action="/notes">
    <table>
      <thead><tr><th>Path</th><th>Notes</th></tr></thead>
      <tbody>{file_rows}</tbody>
    </table>
    <button type="submit">Save file notes</button><span class="note-hint">Unsaved note edits pause auto-refresh.</span>
  </form>
</body>
</html>"""


def main() -> None:
    """Start the HTTP API server."""
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()

    CohortRequestHandler.redis_url = args.redis_url or config.redis_url
    CohortRequestHandler.stream_name = args.stream or config.stream_name

    server = ThreadingHTTPServer((args.host, args.port), CohortRequestHandler)
    CONSOLE.print(
        f"[green]HTTP API listening[/green] http://{args.host}:{args.port} "
        f"stream={CohortRequestHandler.stream_name}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
