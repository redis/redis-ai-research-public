import json
from collections import Counter
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def analyze_event_logs(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze event logs and return a dictionary of visualizations and insights.

    Args:
        events: List of event dictionaries from the log file

    Returns:
        Dictionary with named visualizations and insights
    """

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

    def parse_payload(payload):
        if payload is None:
            return None
        if isinstance(payload, dict) or isinstance(payload, list):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except Exception:
                try:
                    import ast

                    return ast.literal_eval(payload)
                except Exception:
                    return payload
        return payload

    def extract_text_length(text):
        """Extract length of text from various payload fields"""
        if isinstance(text, str):
            return len(text)
        elif isinstance(text, dict):
            # Look for common text fields
            for key in ["question", "prefixed_question", "code", "guidance_message"]:
                if key in text and isinstance(text[key], str):
                    return len(text[key])
        return 0

    # Initialize data structures
    cache_stats = {"hits": 0, "misses": 0, "sets": 0}
    error_count = 0
    guidance_used = 0
    total_questions = 0
    event_timings = []
    event_types = Counter()
    senders = Counter()
    payload_sizes = []
    code_sizes = []
    question_sizes = []
    guidance_sizes = []

    # Track start/end events for timing analysis
    start_events = {}
    event_flows = []

    for i, event in enumerate(events):
        # Basic event counting
        event_type = event.get("event_type", "unknown")
        sender = event.get("from", "unknown")
        success = normalize_success(event.get("success", True))

        event_types[event_type] += 1
        senders[sender] += 1

        # Cache analysis
        if "cache" in sender.lower():
            if "hit" in event_type.lower():
                cache_stats["hits"] += 1
            elif "miss" in event_type.lower():
                cache_stats["misses"] += 1
            elif "set" in event_type.lower():
                cache_stats["sets"] += 1

        # Error counting
        if not success:
            error_count += 1

        # Retry detection (look for repeated questions or error patterns)
        if event_type == "end_event" and "user" in sender:
            payload = parse_payload(event.get("payload"))
            if payload and "question" in payload:
                question = payload["question"]
                if question.strip():  # Non-empty question
                    total_questions += 1

        # Guidance usage
        if "guidance" in event_type.lower() or "guidance_message" in str(
            event.get("payload", "")
        ):
            guidance_used += 1

        # Timing analysis
        response_time = normalize_time(event.get("response_time"))
        if response_time is not None and response_time > 0:
            event_timings.append(
                {
                    "timestamp": event.get("timestamp", ""),
                    "sender": sender,
                    "event_type": event_type,
                    "response_time": response_time,
                    "index": i,
                }
            )

        # Start/end event tracking
        if event_type == "start_event":
            start_events[sender] = {
                "start_time": event.get("timestamp"),
                "start_index": i,
                "payload": event.get("payload"),
            }
        elif event_type == "end_event":
            if sender in start_events:
                start_event = start_events[sender]
                total_time = response_time if response_time else 0
                event_flows.append(
                    {
                        "sender": sender,
                        "start_time": start_event["start_time"],
                        "end_time": event.get("timestamp"),
                        "total_time": total_time,
                        "start_payload": start_event["payload"],
                        "end_payload": event.get("payload"),
                    }
                )
                del start_events[sender]

        # Payload size analysis
        payload = parse_payload(event.get("payload"))
        if payload:
            payload_size = extract_text_length(payload)
            if payload_size > 0:
                payload_sizes.append(payload_size)

                # Specific size tracking
                if "code" in str(payload).lower():
                    code_sizes.append(payload_size)
                if "question" in str(payload).lower():
                    question_sizes.append(payload_size)
                if "guidance" in str(payload).lower():
                    guidance_sizes.append(payload_size)

    # Create visualizations
    visualizations = {}

    # 1. Cache Performance
    if cache_stats["hits"] + cache_stats["misses"] > 0:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

        # Cache hit/miss ratio
        cache_data = [cache_stats["hits"], cache_stats["misses"]]
        cache_labels = ["Cache Hits", "Cache Misses"]
        colors = ["#4CAF50", "#FF9800"]

        ax1.pie(
            cache_data,
            labels=cache_labels,
            colors=colors,
            autopct="%1.1f%%",
            startangle=90,
        )
        ax1.set_title("Cache Hit/Miss Ratio")

        # Cache operations breakdown
        operations = list(cache_stats.keys())
        counts = list(cache_stats.values())
        ax2.bar(operations, counts, color=["#4CAF50", "#FF9800", "#2196F3"])
        ax2.set_title("Cache Operations Breakdown")
        ax2.set_ylabel("Count")

        plt.tight_layout()
        visualizations["cache_performance"] = fig

    # 2. Error and Success Analysis
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Success vs Error
    success_count = len(
        [e for e in events if normalize_success(e.get("success", True))]
    )
    error_data = [success_count, error_count]
    error_labels = ["Success", "Error"]
    error_colors = ["#4CAF50", "#F44336"]

    ax1.pie(
        error_data,
        labels=error_labels,
        colors=error_colors,
        autopct="%1.1f%%",
        startangle=90,
    )
    ax1.set_title("Success vs Error Rate")

    # Top event types
    top_events = event_types.most_common(8)
    event_names = [e[0] for e in top_events]
    event_counts = [e[1] for e in top_events]

    ax2.barh(event_names, event_counts, color="#2196F3")
    ax2.set_title("Most Common Event Types")
    ax2.set_xlabel("Count")

    plt.tight_layout()
    visualizations["error_analysis"] = fig

    # 3. Timing Analysis
    if event_timings:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

        # Timeline of events
        df_timings = pd.DataFrame(event_timings)
        df_timings["timestamp"] = pd.to_datetime(df_timings["timestamp"])

        ax1.scatter(
            df_timings["timestamp"], df_timings["response_time"], alpha=0.6, s=50
        )
        ax1.set_title("Event Response Times Over Time")
        ax1.set_ylabel("Response Time (seconds)")
        ax1.set_xlabel("Timestamp")
        ax1.tick_params(axis="x", rotation=45)

        # Response time distribution
        ax2.hist(df_timings["response_time"], bins=20, alpha=0.7, color="#2196F3")
        ax2.set_title("Response Time Distribution")
        ax2.set_xlabel("Response Time (seconds)")
        ax2.set_ylabel("Frequency")

        plt.tight_layout()
        visualizations["timing_analysis"] = fig

    # 4. Sender Activity
    if senders:
        fig, ax = plt.subplots(figsize=(10, 6))

        top_senders = senders.most_common(10)
        sender_names = [s[0] for s in top_senders]
        sender_counts = [s[1] for s in top_senders]

        bars = ax.bar(sender_names, sender_counts, color="#9C27B0")
        ax.set_title("Event Activity by Sender")
        ax.set_ylabel("Event Count")
        ax.set_xlabel("Sender")
        ax.tick_params(axis="x", rotation=45)

        # Add value labels on bars
        for bar, count in zip(bars, sender_counts):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.1,
                str(count),
                ha="center",
                va="bottom",
            )

        plt.tight_layout()
        visualizations["sender_activity"] = fig

    # 5. Payload Size Analysis
    if payload_sizes:
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(12, 10))

        # Overall payload size distribution
        ax1.hist(payload_sizes, bins=20, alpha=0.7, color="#FF5722")
        ax1.set_title("Payload Size Distribution")
        ax1.set_xlabel("Payload Size (characters)")
        ax1.set_ylabel("Frequency")

        # Code size analysis
        if code_sizes:
            ax2.hist(code_sizes, bins=15, alpha=0.7, color="#795548")
            ax2.set_title("Generated Code Size Distribution")
            ax2.set_xlabel("Code Size (characters)")
            ax2.set_ylabel("Frequency")

        # Question size analysis
        if question_sizes:
            ax3.hist(question_sizes, bins=15, alpha=0.7, color="#607D8B")
            ax3.set_title("Question Size Distribution")
            ax3.set_xlabel("Question Size (characters)")
            ax3.set_ylabel("Frequency")

        # Guidance size analysis
        if guidance_sizes:
            ax4.hist(guidance_sizes, bins=15, alpha=0.7, color="#E91E63")
            ax4.set_title("Guidance Size Distribution")
            ax4.set_xlabel("Guidance Size (characters)")
            ax4.set_ylabel("Frequency")

        plt.tight_layout()
        visualizations["payload_analysis"] = fig

    # 6. Event Flow Timeline
    if event_flows:
        fig, ax = plt.subplots(figsize=(14, 8))

        # Create a timeline of major events
        flow_data = []
        for flow in event_flows:
            if flow["total_time"] > 0:
                flow_data.append(
                    {
                        "sender": flow["sender"],
                        "total_time": flow["total_time"],
                        "start_time": flow["start_time"],
                    }
                )

        if flow_data:
            df_flows = pd.DataFrame(flow_data)
            df_flows["start_time"] = pd.to_datetime(df_flows["start_time"])
            df_flows = df_flows.sort_values("start_time")

            # Create a stacked timeline
            y_pos = 0
            for _, row in df_flows.iterrows():
                ax.barh(
                    y_pos, row["total_time"], left=0, label=row["sender"], alpha=0.7
                )
                ax.text(
                    row["total_time"] + 0.1,
                    y_pos,
                    f"{row['total_time']:.2f}s",
                    va="center",
                )
                y_pos += 1

            ax.set_title("Event Flow Timeline")
            ax.set_xlabel("Time (seconds)")
            ax.set_ylabel("Events")
            ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left")

        plt.tight_layout()
        visualizations["event_flow"] = fig

    # 7. Summary Statistics
    summary_stats = {
        "total_events": len(events),
        "total_questions": total_questions,
        "error_rate": f"{(error_count / len(events) * 100):.1f}%" if events else "0%",
        "cache_hit_rate": (
            f"{(cache_stats['hits'] / (cache_stats['hits'] + cache_stats['misses']) * 100):.1f}%"
            if (cache_stats["hits"] + cache_stats["misses"]) > 0
            else "0%"
        ),
        "guidance_usage": (
            f"{(guidance_used / max(total_questions, 1) * 100):.1f}%"
            if total_questions > 0
            else "0%"
        ),
        "avg_response_time": (
            f"{np.mean([e['response_time'] for e in event_timings]):.3f}s"
            if event_timings
            else "0s"
        ),
        "max_response_time": (
            f"{max([e['response_time'] for e in event_timings]):.3f}s"
            if event_timings
            else "0s"
        ),
        "avg_payload_size": (
            f"{np.mean(payload_sizes):.0f} chars" if payload_sizes else "0 chars"
        ),
        "total_cache_operations": cache_stats["hits"]
        + cache_stats["misses"]
        + cache_stats["sets"],
    }

    # Create summary visualization
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axis("off")

    summary_text = f"""
    Event Log Analysis Summary
    
    📊 Total Events: {summary_stats['total_events']}
    ❓ Total Questions: {summary_stats['total_questions']}
    ❌ Error Rate: {summary_stats['error_rate']}
    🎯 Cache Hit Rate: {summary_stats['cache_hit_rate']}
    💡 Guidance Usage: {summary_stats['guidance_usage']}
    ⏱️ Average Response Time: {summary_stats['avg_response_time']}
    🐌 Max Response Time: {summary_stats['max_response_time']}
    📝 Average Payload Size: {summary_stats['avg_payload_size']}
    🔄 Total Cache Operations: {summary_stats['total_cache_operations']}
    """

    ax.text(
        0.1,
        0.9,
        summary_text,
        transform=ax.transAxes,
        fontsize=12,
        verticalalignment="top",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.5),
    )

    visualizations["summary_stats"] = fig

    return visualizations
