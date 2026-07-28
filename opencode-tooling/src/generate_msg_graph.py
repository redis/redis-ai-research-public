#!/usr/bin/env python3
"""Generate a graph visualization of opencode session messages with system prompts and MCP servers using Rich."""

import json
import os
import sqlite3
from pathlib import Path
from collections import defaultdict
from rich.console import Console
from rich.tree import Tree
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.table import Table
from rich import box

console = Console()

# Known agent prompts (from /config endpoint)
AGENT_PROMPTS = {
    "build": """The default agent. Executes tools based on configured permissions.""",

    "plan": """Plan mode. Disallows all edit tools.""",

    "general": """General-purpose agent for researching complex questions and executing multi-step tasks. Use this agent to execute multiple units of work in parallel.""",

    "explore": """Fast agent specialized for exploring codebases. Use this when you need to quickly find files by patterns (eg. "src/components/**/*.tsx"), search code for keywords (eg. "API endpoints"), or answer questions about the codebase (eg. "how do API endpoints work?"). When calling this agent, specify the desired thoroughness level: "quick" for basic searches, "medium" for moderate exploration, or "very thorough" for comprehensive analysis across multiple locations and naming conventions.""",

    "ftprofile-auditor": """The ftprofile files are in the folder BASE_FOLDER/ftprofile-input-files. Always start by calling the MCP server tool `get_ftprofile_stats` to get the statistics about the profile and `get_ftprofile_structure` to get a high level overview of the elements in the file. Use this information to answer questions. File read should be a last resort. If you need to read the file, strategies for how to read the data files are in subagent `ftprofile-fileread`. BASE_FOLDER/docs contains resources that should be read to interpret the ft profile and perform the analysis. The instructions on what to do and to report the findings can be found in BASE_FOLDER/docs/ftprofile-instructions.""",

    "ftprofile-fileread": """1. Get the length of the file
2. If the file is less than 30000 lines then read all of it in and analyze it
3. If it is greater than 30000 lines then read in 30000 lines at a time as a chunk and analyze it, determine what sections need to be read""",

    "compaction": """You are a helpful AI assistant tasked with summarizing conversations.

When asked to summarize, provide a detailed but concise summary of the conversation.
Focus on information that would be helpful for continuing the conversation, including:
- What was done
- What is currently being worked on
- Which files are being modified
- What needs to be done next
- Key user requests, constraints, or preferences that should persist
- Important technical decisions and why they were made

Your summary should be comprehensive enough to provide context but concise enough to be quickly understood.""",

    "title": """You are a title generator. You output ONLY a thread title. Nothing else.""",

    "summary": """Summarize what was done in this conversation. Write like a pull request description.

Rules:
- 2-3 sentences max
- Describe the changes made, not the process
- Do not mention running tests, builds, or other validation steps
- Do not explain what the user asked for
- Write in first person (I added..., I fixed...)
- Never ask questions or add new questions
- If the conversation ends with an unanswered question to the user, preserve that exact question
- If the conversation ends with an imperative statement or request to the user (e.g. "Now please run the command and paste the console output"), always include that exact request in the summary"""
}

# Known MCP servers and their tools
MCP_SERVERS = {
    "ftprofile-stats-server": {
        "description": "FT.profile statistics and analysis",
        "tools": ["get_ftprofile_stats", "get_ftprofile_structure", "get_ftprofile_iterators"]
    }
}

def load_agent_prompts_from_config():
    """Try to load agent prompts from opencode config files."""
    prompts = AGENT_PROMPTS.copy()

    # Try project config
    project_config_paths = [
        Path.cwd() / "opencode.json",
        Path.cwd() / "opencode.jsonc",
        Path.cwd() / ".opencode" / "config.json",
    ]

    # Try user config
    user_config_paths = [
        Path.home() / ".config" / "opencode" / "config.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".opencode" / "config.json",
    ]

    all_paths = project_config_paths + user_config_paths

    for config_path in all_paths:
        if config_path.exists():
            try:
                with open(config_path) as f:
                    content = f.read()
                    # Remove comments for jsonc
                    lines = []
                    for line in content.split('\n'):
                        if '//' in line:
                            line = line[:line.index('//')]
                        lines.append(line)
                    config = json.loads('\n'.join(lines))

                    # Extract agent prompts from config
                    if 'agent' in config:
                        for agent_name, agent_config in config['agent'].items():
                            if isinstance(agent_config, dict) and 'prompt' in agent_config:
                                prompts[agent_name] = agent_config['prompt']

                    # Extract MCP server info
                    if 'mcp' in config:
                        for server_name, server_config in config['mcp'].items():
                            if isinstance(server_config, dict):
                                MCP_SERVERS[server_name] = {
                                    'description': server_config.get('description', 'MCP Server'),
                                    'tools': server_config.get('tools', [])
                                }
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load config from {config_path}: {e}[/yellow]")

    return prompts

def parse_mcp_tool(tool_name):
    """Parse an MCP tool name to extract server and tool.

    MCP tools have format: <server-name>_<tool-name>
    Example: ftprofile-stats-server_get_ftprofile_stats
    """
    # Split on underscore - first part is server, rest is tool
    parts = tool_name.split('_')
    if len(parts) >= 2:
        server_name = parts[0]
        tool_name_only = '_'.join(parts[1:])

        # Check if it's a known MCP server
        if server_name in MCP_SERVERS:
            return {
                'is_mcp': True,
                'server': server_name,
                'tool': tool_name_only,
                'full_name': tool_name
            }

    # Not an MCP tool
    return {
        'is_mcp': False,
        'server': None,
        'tool': tool_name,
        'full_name': tool_name
    }

def get_message_details(msg_id, max_length=500):
    """Extract detailed content from message parts including MCP info."""
    part_dir = Path.home() / f".local/share/opencode/storage/part/{msg_id}"

    if not part_dir.exists():
        return None, [], []

    contents = []
    all_tools = []
    mcp_calls = []  # List of MCP server calls
    reasoning_parts = []
    text_parts = []

    # Read all parts
    for part_file in sorted(part_dir.glob("*.json")):
        try:
            with open(part_file) as f:
                part = json.load(f)
                part_type = part.get('type', '')

                if part_type == 'text' and 'text' in part:
                    text = part['text'].strip()
                    if text:
                        text_parts.append(text)
                elif part_type == 'tool':
                    tool_name = part.get('tool', 'unknown')
                    all_tools.append(tool_name)

                    # Parse MCP info
                    mcp_info = parse_mcp_tool(tool_name)
                    if mcp_info['is_mcp']:
                        mcp_calls.append({
                            'server': mcp_info['server'],
                            'tool': mcp_info['tool'],
                            'input': part.get('state', {}).get('input', {}),
                            'status': part.get('state', {}).get('status', 'unknown')
                        })
                elif part_type == 'reasoning' and 'text' in part:
                    reasoning_parts.append(part['text'].strip())
        except Exception:
            continue

    # Build result with full content
    result_parts = []

    # Add reasoning if present
    if reasoning_parts:
        full_reasoning = " ".join(reasoning_parts)
        result_parts.append(f"[reasoning: {full_reasoning}]")

    # Add text content
    if text_parts:
        full_text = " ".join(text_parts)
        result_parts.append(full_text)

    # Combine all parts
    result = " | ".join(result_parts) if result_parts else ""

    # Apply length limit if specified
    if max_length and len(result) > max_length:
        result = result[:max_length-3] + "..."

    # Add tool calls summary
    if all_tools:
        tools_str = f"[tools: {', '.join(all_tools)}]"
        if result:
            result += f" {tools_str}"
        else:
            result = tools_str

    return result if result else None, all_tools, mcp_calls

def get_message_details_from_db_parts(parts, max_length=500):
    """Extract detailed content from opencode.db part rows."""
    result_parts = []
    all_tools = []
    mcp_calls = []
    reasoning_parts = []
    text_parts = []

    for part in parts:
        part_type = part.get('type', '')

        if part_type == 'text' and 'text' in part:
            text = part['text'].strip()
            if text:
                text_parts.append(text)
        elif part_type == 'reasoning' and 'text' in part:
            text = part['text'].strip()
            if text:
                reasoning_parts.append(text)
        elif part_type == 'tool':
            tool_name = part.get('tool', 'unknown')
            all_tools.append(tool_name)

            mcp_info = parse_mcp_tool(tool_name)
            if mcp_info['is_mcp']:
                state = part.get('state', {})
                mcp_calls.append({
                    'server': mcp_info['server'],
                    'tool': mcp_info['tool'],
                    'input': state.get('input', {}),
                    'status': state.get('status', 'unknown')
                })

    if reasoning_parts:
        result_parts.append(f"[reasoning: {' '.join(reasoning_parts)}]")
    if text_parts:
        result_parts.append(" ".join(text_parts))

    result = " | ".join(result_parts) if result_parts else ""
    if max_length and len(result) > max_length:
        result = result[:max_length-3] + "..."

    if all_tools:
        tools_str = f"[tools: {', '.join(all_tools)}]"
        if result:
            result += f" {tools_str}"
        else:
            result = tools_str

    return result if result else None, all_tools, mcp_calls

def build_message_graph_from_db(session_id):
    """Build a message graph from ~/.local/share/opencode/opencode.db."""
    db_path = Path.home() / ".local/share/opencode/opencode.db"

    if not db_path.is_file():
        console.print(f"[red]OpenCode database not found: {db_path}[/red]")
        return None

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        message_rows = cur.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ).fetchall()

        if not message_rows:
            console.print(f"[red]No messages found in opencode.db for session: {session_id}[/red]")
            return None

        part_rows = cur.execute(
            "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

    parts_by_message = defaultdict(list)
    for message_id, raw_part_data in part_rows:
        try:
            parts_by_message[message_id].append(json.loads(raw_part_data))
        except json.JSONDecodeError:
            continue

    messages = {}
    roots = []
    current_user_id = None
    previous_message_id = None

    for msg_id, raw_message_data in message_rows:
        data = json.loads(raw_message_data)
        role = data.get('role', 'unknown')
        model_data = data.get('model') or {}
        model = data.get('modelID') or model_data.get('modelID') or model_data.get('id') or 'N/A'

        if role == 'user':
            parent_id = None
            roots.append(msg_id)
            current_user_id = msg_id
        else:
            parent_id = current_user_id or previous_message_id

        content, tools, mcp_calls = get_message_details_from_db_parts(parts_by_message[msg_id])
        messages[msg_id] = {
            'id': msg_id,
            'parent_id': parent_id,
            'role': role,
            'created': data.get('time', {}).get('created', 0),
            'model': model,
            'agent': data.get('agent', 'N/A'),
            'children': [],
            'content': content,
            'tools': tools,
            'mcp_calls': mcp_calls
        }
        previous_message_id = msg_id

    for msg_id, msg in messages.items():
        parent_id = msg['parent_id']
        if parent_id and parent_id in messages:
            messages[parent_id]['children'].append(msg_id)

    return messages, roots

def build_message_graph(session_id):
    """Build a message graph from session storage."""
    msg_dir = Path.home() / f".local/share/opencode/storage/message/{session_id}"

    if not msg_dir.exists():
        console.print(f"[yellow]Session directory not found: {msg_dir}[/yellow]")
        console.print("[dim]Falling back to ~/.local/share/opencode/opencode.db...[/dim]")
        return build_message_graph_from_db(session_id)

    # Load all messages
    messages = {}
    roots = []

    for msg_file in sorted(msg_dir.glob("*.json")):
        with open(msg_file) as f:
            data = json.load(f)
            msg_id = data['id']
            parent_id = data.get('parentID')

            messages[msg_id] = {
                'id': msg_id,
                'parent_id': parent_id,
                'role': data['role'],
                'created': data['time']['created'],
                'model': data.get('modelID', 'N/A'),
                'agent': data.get('agent', 'N/A'),
                'children': [],
                'content': None,
                'tools': [],
                'mcp_calls': []
            }

            if parent_id is None:
                roots.append(msg_id)

    # Build parent-child relationships
    for msg_id, msg in messages.items():
        if msg['parent_id'] and msg['parent_id'] in messages:
            messages[msg['parent_id']]['children'].append(msg_id)

    # Load content for all messages
    console.print("[dim]Loading message contents and MCP calls...[/dim]")
    for msg_id in messages:
        content, tools, mcp_calls = get_message_details(msg_id)
        messages[msg_id]['content'] = content
        messages[msg_id]['tools'] = tools
        messages[msg_id]['mcp_calls'] = mcp_calls

    return messages, roots

def build_rich_tree(messages, roots, agent_prompts, show_system=True, show_content=True, show_mcp=True):
    """Build a Rich tree with message content, system prompts, and MCP info."""
    root_tree = Tree("[bold blue]📁 Session Root[/bold blue]")

    def add_message_to_tree(tree, msg_id, is_last=True):
        msg = messages[msg_id]
        short_id = msg_id.split('_')[1][:8] if '_' in msg_id else msg_id[:8]

        if msg['role'] == 'user':
            # User message styling
            label = Text()
            label.append("👤 ", style="blue")
            label.append(f"[{short_id}] ", style="dim")
            label.append("User", style="bold cyan")

            node = tree.add(label)

            # Add content
            if show_content and msg.get('content'):
                content_text = Text(f"💬 {msg['content']}", style="cyan")
                node.add(content_text)
        else:
            # Assistant message styling
            label = Text()
            label.append("🤖 ", style="green")
            label.append(f"[{short_id}] ", style="dim")
            label.append(f"{msg['agent']}", style="bold green")
            label.append(f" ({msg['model']})", style="dim")

            node = tree.add(label)

            # Add system prompt
            if show_system and msg['agent'] in agent_prompts:
                agent_name = msg['agent']
                system_prompt = agent_prompts[agent_name]

                # Create panel for system prompt
                prompt_text = Text(system_prompt[:300])
                if len(system_prompt) > 300:
                    prompt_text.append("...")

                prompt_panel = Panel(
                    prompt_text,
                    title=f"[yellow]📋 System Prompt: {agent_name}[/yellow]",
                    border_style="yellow",
                    box=box.ROUNDED
                )
                node.add(prompt_panel)

            # Add MCP calls
            if show_mcp and msg.get('mcp_calls'):
                mcp_text = Text()
                mcp_text.append("🔌 MCP Servers Called:\n", style="bold magenta")

                for i, mcp_call in enumerate(msg['mcp_calls']):
                    server = mcp_call['server']
                    tool = mcp_call['tool']
                    status = mcp_call['status']

                    # Get server description
                    server_desc = MCP_SERVERS.get(server, {}).get('description', 'MCP Server')

                    mcp_text.append(f"  {i+1}. ", style="dim")
                    mcp_text.append(f"{server}", style="magenta")
                    mcp_text.append(f" → {tool}", style="bright_magenta")
                    mcp_text.append(f" ({status})", style="green" if status == "completed" else "red")
                    mcp_text.append("\n")

                    # Show input parameters (truncated)
                    input_params = mcp_call.get('input', {})
                    if input_params:
                        params_str = json.dumps(input_params)[:60]
                        if len(json.dumps(input_params)) > 60:
                            params_str += "..."
                        mcp_text.append(f"     Input: {params_str}\n", style="dim")

                mcp_panel = Panel(
                    mcp_text,
                    title="[magenta]🔌 MCP Tool Calls[/magenta]",
                    border_style="magenta",
                    box=box.ROUNDED
                )
                node.add(mcp_panel)

            # Add content
            if show_content and msg.get('content'):
                content_text = Text(f"💬 {msg['content']}", style="white")
                node.add(content_text)

        # Process children
        children = msg['children']
        for i, child_id in enumerate(children):
            add_message_to_tree(node, child_id, i == len(children) - 1)

    for root_id in roots:
        add_message_to_tree(root_tree, root_id, True)

    return root_tree

def print_statistics(messages, roots):
    """Print session statistics in a rich table."""
    table = Table(title="[bold]Session Statistics[/bold]", box=box.DOUBLE_EDGE)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Total Messages", str(len(messages)))
    table.add_row("Root Messages (User Prompts)", str(len(roots)))
    table.add_row("User Messages", str(sum(1 for m in messages.values() if m['role'] == 'user')))
    table.add_row("Assistant Messages", str(sum(1 for m in messages.values() if m['role'] == 'assistant')))

    # Count by agent
    agent_counts = {}
    for msg in messages.values():
        if msg['role'] == 'assistant':
            agent = msg['agent']
            agent_counts[agent] = agent_counts.get(agent, 0) + 1

    if agent_counts:
        table.add_row("", "")
        table.add_row("[bold]Messages by Agent[/bold]", "")
        for agent, count in sorted(agent_counts.items()):
            table.add_row(f"  {agent}", str(count))

    # MCP server usage
    mcp_usage: dict[str, dict] = {}
    for msg in messages.values():
        for mcp_call in msg.get('mcp_calls', []):
            server = mcp_call['server']
            if server not in mcp_usage:
                mcp_usage[server] = {'calls': 0, 'messages': set()}
            mcp_usage[server]['calls'] += 1
            mcp_usage[server]['messages'].add(msg['id'])

    if mcp_usage:
        table.add_row("", "")
        table.add_row("[bold magenta]MCP Server Usage[/bold magenta]", "")
        for server, stats in sorted(mcp_usage.items()):
            desc = MCP_SERVERS.get(server, {}).get('description', 'MCP Server')
            table.add_row(f"  🔌 {server}", f"{stats['calls']} calls in {len(stats['messages'])} msgs")

    console.print(table)

def print_agent_prompts_summary(agent_prompts):
    """Print summary of available agent prompts."""
    table = Table(title="[bold]Available Agents[/bold]", box=box.SIMPLE)
    table.add_column("Agent", style="cyan")
    table.add_column("Prompt Preview", style="dim")

    for agent, prompt in sorted(agent_prompts.items()):
        preview = prompt[:60].replace('\n', ' ')
        if len(prompt) > 60:
            preview += "..."
        table.add_row(agent, preview)

    console.print(table)

def print_mcp_servers():
    """Print MCP server information."""
    if not MCP_SERVERS:
        return

    table = Table(title="[bold magenta]Available MCP Servers[/bold magenta]", box=box.SIMPLE)
    table.add_column("Server", style="magenta")
    table.add_column("Description", style="dim")
    table.add_column("Tools", style="cyan")

    for server, info in sorted(MCP_SERVERS.items()):
        tools_str = ", ".join(info.get('tools', [])[:3])
        if len(info.get('tools', [])) > 3:
            tools_str += "..."
        table.add_row(server, info.get('description', ''), tools_str)

    console.print(table)

def generate_dot_graph(messages, roots, agent_prompts, session_id):
    """Generate Graphviz DOT format with content and agent info."""
    lines = ["digraph MessageGraph {"]
    lines.append(f'  label="Session: {session_id[:20]}...";')
    lines.append("  rankdir=TB;")
    lines.append("  node [shape=box];")
    lines.append("")

    # Define nodes
    for msg_id, msg in messages.items():
        short_id = msg_id.split('_')[1][:8] if '_' in msg_id else msg_id[:8]

        if msg['role'] == 'user':
            color = "lightblue"
        else:
            color = "lightgreen"

        # Build label with content and agent
        label_parts = [f"{msg['role'][:4]}: {short_id}"]

        if msg['role'] == 'assistant' and msg['agent']:
            label_parts.append(f"agent: {msg['agent'][:15]}")

        # Add MCP indicator
        if msg.get('mcp_calls'):
            mcp_servers = list(set(mcp['server'] for mcp in msg['mcp_calls']))
            label_parts.append(f"MCP: {','.join(mcp_servers)[:20]}")

        if msg.get('content'):
            content = msg['content'][:30].replace('"', '\\"').replace('\n', '\\n')
            if len(msg['content']) > 30:
                content += "..."
            label_parts.append(content)

        label = "\\n".join(label_parts)
        lines.append(f'  "{msg_id}" [style=filled, fillcolor={color}, label="{label}"];')

    lines.append("")

    # Define edges
    for msg_id, msg in messages.items():
        if msg['parent_id']:
            lines.append(f'  "{msg["parent_id"]}" -> "{msg_id}";')

    lines.append("}")
    return "\n".join(lines)

def export_with_prompts(messages, roots, agent_prompts, output_file="session_with_prompts.json"):
    """Export full session data including system prompts and MCP calls."""
    export_data = {
        "session_info": {
            "total_messages": len(messages),
            "root_messages": len(roots),
            "user_messages": sum(1 for m in messages.values() if m['role'] == 'user'),
            "assistant_messages": sum(1 for m in messages.values() if m['role'] == 'assistant')
        },
        "agent_prompts": agent_prompts,
        "mcp_servers": MCP_SERVERS,
        "conversations": []
    }

    def build_conversation_tree(msg_id):
        msg = messages[msg_id]
        conv = {
            "id": msg_id,
            "role": msg['role'],
            "agent": msg['agent'] if msg['role'] == 'assistant' else None,
            "model": msg['model'] if msg['role'] == 'assistant' else None,
            "content": msg.get('content'),
            "system_prompt": agent_prompts.get(msg['agent']) if msg['role'] == 'assistant' else None,
            "tools": msg.get('tools', []),
            "mcp_calls": msg.get('mcp_calls', []),
            "children": [build_conversation_tree(child_id) for child_id in msg['children']]
        }
        return conv

    for root_id in roots:
        export_data["conversations"].append(build_conversation_tree(root_id))

    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)

    console.print(f"\n[green]✓ Exported full session data to:[/green] [cyan]{output_file}[/cyan]")


def main():
    """Run the message graph CLI."""
    import sys

    if len(sys.argv) > 1:
        session_id = sys.argv[1]
    else:
        session_id = "ses_3914e550bffey71o717DjnTif4"

    # Load agent prompts
    console.print("\n[dim]Loading agent prompts and MCP servers...[/dim]")
    agent_prompts = load_agent_prompts_from_config()

    # Header
    console.print()
    console.print(Panel(
        f"[bold blue]Message Graph for Session[/bold blue]\n[dim]{session_id}[/dim]",
        box=box.DOUBLE,
        border_style="blue"
    ))
    console.print()

    result = build_message_graph(session_id)
    if result:
        messages, roots = result

        # Print MCP servers info
        print_mcp_servers()
        console.print()

        # Print tree view with content, system prompts, and MCP calls
        console.print("[bold cyan]Conversation Tree:[/bold cyan]")
        console.print("─" * 80)
        rich_tree = build_rich_tree(messages, roots, agent_prompts, show_system=True, show_content=True, show_mcp=True)
        console.print(rich_tree)
        console.print()

        # Statistics
        console.print()
        print_statistics(messages, roots)
        console.print()

        # Agent prompts summary
        print_agent_prompts_summary(agent_prompts)
        console.print()

        # Generate DOT format
        console.print("[bold cyan]Graphviz DOT Format:[/bold cyan]")
        console.print("[dim]Save to .dot file and run: dot -Tpng -o graph.png file.dot[/dim]")
        console.print("─" * 80)
        console.print(Syntax(generate_dot_graph(messages, roots, agent_prompts, session_id), "dot", theme="monokai"))
        console.print()

        # Export to JSON with full prompts and MCP calls
        export_with_prompts(messages, roots, agent_prompts, f"{session_id}_export.json")


if __name__ == "__main__":
    main()
