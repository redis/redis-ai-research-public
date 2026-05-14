import html
import json
import uuid
from typing import Any, Dict, List


def render_event_log_html(events: List[Dict[str, Any]]) -> str:
    """
    Render a list of event dicts (from jsonl) as a compact, color-coded, nested HTML timeline.
    - Success: green, Error: red, Neutral: gray
    - Show sender, event_type, response_time, nesting (if any)
    - Payload in <details>
    - Designed for Jupyter notebook display
    """

    def pretty(obj):
        try:
            return html.escape(json.dumps(obj, indent=2, ensure_ascii=False))
        except Exception:
            return html.escape(str(obj))

    def parse_payload(payload):
        # Try to parse string payloads that look like dicts
        if payload is None:
            return None
        if isinstance(payload, dict) or isinstance(payload, list):
            return payload
        if isinstance(payload, str):
            # Try to parse as JSON
            try:
                return json.loads(payload)
            except Exception:
                # Try to eval as dict (single quotes)
                try:
                    import ast

                    return ast.literal_eval(payload)
                except Exception:
                    return payload
        return payload

    def normalize_success(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def normalize_time(val):
        try:
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                return float(val)
        except (ValueError, TypeError):
            return None
        return None

    def unique_id():
        return str(uuid.uuid4())

    def build_tree(events):
        stack = []
        root = []
        for ev in events:
            if ev.get("event_type") == "start_event":
                node = {"event": ev, "children": []}
                if stack:
                    stack[-1]["children"].append(node)
                else:
                    root.append(node)
                stack.append(node)
            elif ev.get("event_type") == "end_event":
                if stack:
                    stack[-1]["end_event"] = ev
                    stack.pop()
                else:
                    root.append({"event": ev, "children": []})
            else:
                node = {"event": ev, "children": []}
                if stack:
                    stack[-1]["children"].append(node)
                else:
                    root.append(node)
        return root

    def render_node(node, depth=0):
        ev = node["event"]
        children = node.get("children", [])
        end_event = node.get("end_event")

        # Color by success
        success = normalize_success(ev.get("success", True))
        color = "#d4ffd4" if success else "#ffd4d4"
        border = "#4caf50" if success else "#f44336"
        if ev.get("event_type") == "end_event":
            color = "#e0e0e0"
            border = "#888"

        # Compact info
        sender = html.escape(str(ev.get("from", "?")))
        event_type = html.escape(str(ev.get("event_type", "?")))
        response_time = normalize_time(ev.get("response_time"))
        response_time_str = (
            f"{response_time:.3f}s"
            if response_time is not None and response_time > 0
            else ""
        )

        # Main line
        html_parts = [
            f'<div style="margin-left:{depth*18}px; border-left:4px solid {border}; background:{color}; padding:4px 8px; margin-bottom:2px; border-radius:4px; font-size:13px; line-height:1.4;">',
            f'<b>{sender}</b> <span style="color:#888">→</span> <b>{event_type}</b>',
        ]
        if response_time_str:
            html_parts.append(f' <span style="color:#888">[{response_time_str}]</span>')

        # Details for payload from start_event
        payload = parse_payload(ev.get("payload"))
        if payload not in (None, {}, []):
            html_parts.append(
                f' <details style="display:inline-block; margin-left:8px;"><summary style="cursor:pointer; color:#555;">start payload</summary><pre style="max-width:600px; overflow-x:auto; background:#f8f8f8; border:1px solid #eee; border-radius:3px; padding:4px;">{pretty(payload)}</pre></details>'
            )

        # If this is a start_event with an end_event, show total time and end payload
        if end_event:
            et = normalize_time(end_event.get("response_time"))
            if et is not None and et > 0:
                html_parts.append(
                    f' <span style="color:#2196f3;">⏱ total: {et:.3f}s</span>'
                )

            # Show end_event payload if it exists
            end_payload = parse_payload(end_event.get("payload"))
            if end_payload not in (None, {}, []):
                html_parts.append(
                    f' <details style="display:inline-block; margin-left:8px;"><summary style="cursor:pointer; color:#555;">end payload</summary><pre style="max-width:600px; overflow-x:auto; background:#f8f8f8; border:1px solid #eee; border-radius:3px; padding:4px;">{pretty(end_payload)}</pre></details>'
                )
        html_parts.append("</div>")

        # Children
        for child in children:
            html_parts.append(render_node(child, depth + 1))
        return "".join(html_parts)

    # Build tree and render
    tree = build_tree(events)
    html_out = [
        '<div style="font-family:monospace,monospace; background:#fafbfc; padding:8px; border-radius:6px; border:1px solid #eee;">'
    ]
    for node in tree:
        html_out.append(render_node(node))
    html_out.append("</div>")
    return "".join(html_out)
