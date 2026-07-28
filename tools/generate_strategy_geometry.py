"""Generate the strategy-geometry figure for the agent-memory article.

The script reads the locked LongMemEval runs and cached embeddings from the
evaluation repository. It compares one conversation under Instruct and Remis
in one shared t-SNE projection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from sklearn.manifold import TSNE


DEFAULT_QUESTION_ID = "gpt4_d6585ce9"
INSTRUCT_RUN = (
    "20260402-1117-redis-instruct-existinglimit20-modelgpt-4o-"
    "extractionmodelgpt-4o"
)
REMIS_RUN = "20260402-1117-rag-mem-topk20-modelgpt-4o"

INK = "#171716"
MUTED = "#68645d"
LINE = "#cfc6b8"
PAPER = "#f5f1e8"
WHITE = "#fffdf8"
RED = "#d52b1e"
ORANGE = "#e8820c"
PATH = "#315b63"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-output", type=Path)
    parser.add_argument("--question-id", default=DEFAULT_QUESTION_ID)
    return parser.parse_args()


def memory_texts(answer: dict) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for memory in answer.get("memories") or []:
        text = memory if isinstance(memory, str) else str(memory)
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def main() -> int:
    args = parse_args()
    eval_repo = args.eval_repo.resolve()
    analysis_dir = eval_repo / "wip.ignore"
    sys.path.insert(0, str(analysis_dir))

    import compute_run_metrics as metrics  # noqa: PLC0415

    instruct_meta, instruct_answers, _ = metrics._load_run(INSTRUCT_RUN)
    _, remis_answers, _ = metrics._load_run(REMIS_RUN)

    instruct_answer = next(
        row for row in instruct_answers if row.get("question_id") == args.question_id
    )
    remis_answer = next(
        row for row in remis_answers if row.get("question_id") == args.question_id
    )

    dataset_name, split = metrics._resolve_dataset(instruct_meta)
    adapter_cls = metrics.ADAPTERS[dataset_name]
    examples = adapter_cls(split=split).load()
    example = next(
        row for row in examples if row.metadata.get("question_id") == args.question_id
    )

    turn_texts: list[str] = []
    turn_records: list[dict] = []
    session_ranges: list[range] = []
    for session_index, session in enumerate(example.sessions):
        start = len(turn_texts)
        for turn_index, message in enumerate(session.messages):
            text = message.text.strip()
            if not text:
                continue
            turn_texts.append(text)
            turn_records.append(
                {
                    "session": session_index,
                    "sessionLabel": session.label,
                    "turn": turn_index,
                    "speaker": message.speaker,
                    "text": text,
                }
            )
        session_ranges.append(range(start, len(turn_texts)))

    instruct_texts = memory_texts(instruct_answer)
    remis_texts = memory_texts(remis_answer)

    connection = metrics._get_db()
    turn_vectors = np.asarray(
        metrics._embed_texts(connection, turn_texts, metrics.EMBED_MODEL, "turns"),
        dtype=np.float32,
    )
    instruct_vectors = np.asarray(
        metrics._embed_texts(
            connection, instruct_texts, metrics.EMBED_MODEL, "Instruct memories"
        ),
        dtype=np.float32,
    )
    remis_vectors = np.asarray(
        metrics._embed_texts(
            connection, remis_texts, metrics.EMBED_MODEL, "Remis memories"
        ),
        dtype=np.float32,
    )
    query_text = instruct_answer["question"]
    query_vector = np.asarray(
        metrics._embed_texts(
            connection, [query_text], metrics.EMBED_MODEL, "test query"
        )[0],
        dtype=np.float32,
    )

    combined = np.vstack(
        [turn_vectors, instruct_vectors, remis_vectors, query_vector.reshape(1, -1)]
    )
    normalized = combined / (np.linalg.norm(combined, axis=1, keepdims=True) + 1e-12)
    projection = TSNE(
        n_components=2,
        perplexity=30,
        init="pca",
        learning_rate="auto",
        random_state=0,
    ).fit_transform(normalized)
    turn_points = projection[: len(turn_vectors)]
    instruct_end = len(turn_vectors) + len(instruct_vectors)
    instruct_points = projection[len(turn_vectors) : instruct_end]
    remis_end = instruct_end + len(remis_vectors)
    remis_points = projection[instruct_end:remis_end]
    query_point = projection[-1]

    def nearest_turn_distance(memory_vectors: np.ndarray) -> float:
        turns = turn_vectors / (
            np.linalg.norm(turn_vectors, axis=1, keepdims=True) + 1e-12
        )
        memories = memory_vectors / (
            np.linalg.norm(memory_vectors, axis=1, keepdims=True) + 1e-12
        )
        return float(np.median(1.0 - np.max(memories @ turns.T, axis=1)))

    nearest = {
        "Instruct": nearest_turn_distance(instruct_vectors),
        "Remis": nearest_turn_distance(remis_vectors),
    }

    if args.data_output:
        normalized_turns = turn_vectors / (
            np.linalg.norm(turn_vectors, axis=1, keepdims=True) + 1e-12
        )
        normalized_query = query_vector / (np.linalg.norm(query_vector) + 1e-12)

        def serialize_memories(
            texts: list[str], vectors: np.ndarray, points: np.ndarray
        ) -> list[dict]:
            normalized_memories = vectors / (
                np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-12
            )
            turn_similarity = normalized_memories @ normalized_turns.T
            nearest_turns = np.argmax(turn_similarity, axis=1)
            query_distances = 1.0 - normalized_memories @ normalized_query
            return [
                {
                    "x": float(points[index, 0]),
                    "y": float(points[index, 1]),
                    "text": text[:800],
                    "nearestTurn": int(nearest_turns[index]),
                    "nearestTurnDistance": float(
                        1.0 - turn_similarity[index, nearest_turns[index]]
                    ),
                    "queryDistance": float(query_distances[index]),
                }
                for index, text in enumerate(texts)
            ]

        turns = [
            {
                **record,
                "x": float(turn_points[index, 0]),
                "y": float(turn_points[index, 1]),
                "text": record["text"][:800],
            }
            for index, record in enumerate(turn_records)
        ]
        payload = {
            "projection": "Shared t-SNE projection",
            "question": {
                "x": float(query_point[0]),
                "y": float(query_point[1]),
                "text": query_text,
            },
            "bounds": {
                "xMin": float(projection[:, 0].min()),
                "xMax": float(projection[:, 0].max()),
                "yMin": float(projection[:, 1].min()),
                "yMax": float(projection[:, 1].max()),
            },
            "turns": turns,
            "strategies": {
                "instruct": {
                    "name": "Instruct",
                    "subtitle": "Extracted facts",
                    "memories": serialize_memories(
                        instruct_texts, instruct_vectors, instruct_points
                    ),
                },
                "remis": {
                    "name": "Remis",
                    "subtitle": "Raw conversation chunks",
                    "memories": serialize_memories(
                        remis_texts, remis_vectors, remis_points
                    ),
                },
            },
            "focuses": [
                {
                    "id": "queen-concert",
                    "label": "Close-up 01",
                    "title": "Queen at the Prudential Center",
                    "sessions": [16],
                    "conversation": (
                        "In session 17, the user says they saw Queen with Adam "
                        "Lambert at the Prudential Center in Newark with their "
                        "parents."
                    ),
                    "explanation": (
                        "This is the event asked about by the test question. "
                        "Instruct stores the concert, venue, and companions as "
                        "compact facts around the exchange. Remis retains the "
                        "original turns, so its memory rings sit almost directly "
                        "on the conversation path."
                    ),
                    "includeQuery": True,
                },
                {
                    "id": "billie-eilish-concert",
                    "label": "Close-up 02",
                    "title": "A similar concert memory",
                    "sessions": [0],
                    "conversation": (
                        "In session 1, the user describes a Billie Eilish concert "
                        "at the Wells Fargo Center in Philadelphia with their "
                        "sister."
                    ),
                    "explanation": (
                        "This event is semantically similar to the target concert "
                        "but has a different companion. Instruct separates those "
                        "details into facts; Remis preserves the wording and local "
                        "turn sequence. The pair shows why temporal and entity "
                        "details matter even inside one topic cluster."
                    ),
                    "includeQuery": False,
                },
            ],
        }
        args.data_output.parent.mkdir(parents=True, exist_ok=True)
        args.data_output.write_text(json.dumps(payload, separators=(",", ":")))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "text.color": INK,
            "axes.labelcolor": MUTED,
            "axes.edgecolor": LINE,
        }
    )
    figure, axes = plt.subplots(1, 2, figsize=(16, 7.4), sharex=True, sharey=True)
    figure.patch.set_facecolor(PAPER)

    panels = [
        (
            axes[0],
            instruct_points,
            "Instruct",
            "Extracted facts form a wider cloud around the conversation",
        ),
        (
            axes[1],
            remis_points,
            "Remis",
            "Raw conversation chunks stay close to the turn-by-turn paths",
        ),
    ]

    x_pad = (projection[:, 0].max() - projection[:, 0].min()) * 0.06
    y_pad = (projection[:, 1].max() - projection[:, 1].min()) * 0.08
    x_limits = (
        projection[:, 0].min() - x_pad,
        projection[:, 0].max() + x_pad,
    )
    y_limits = (
        projection[:, 1].min() - y_pad,
        projection[:, 1].max() + y_pad,
    )

    for axis, memory_points, label, subtitle in panels:
        axis.set_facecolor(WHITE)
        for positions in session_ranges:
            indices = list(positions)
            if len(indices) < 2:
                continue
            axis.plot(
                turn_points[indices, 0],
                turn_points[indices, 1],
                color=PATH,
                linewidth=1.15,
                alpha=0.34,
                zorder=1,
            )

        axis.scatter(
            turn_points[:, 0],
            turn_points[:, 1],
            s=13,
            facecolors=WHITE,
            edgecolors=PATH,
            linewidths=0.65,
            alpha=0.9,
            zorder=2,
        )
        axis.scatter(
            memory_points[:, 0],
            memory_points[:, 1],
            s=29,
            facecolors="none",
            edgecolors=ORANGE,
            linewidths=1.0,
            alpha=0.72,
            zorder=3,
        )
        axis.scatter(
            [query_point[0]],
            [query_point[1]],
            marker="*",
            s=210,
            color=RED,
            edgecolors=INK,
            linewidths=0.7,
            zorder=4,
        )

        axis.set_xlim(*x_limits)
        axis.set_ylim(*y_limits)
        axis.set_xticks([])
        axis.set_yticks([])
        axis.set_title(label, loc="left", fontsize=22, fontweight="bold", pad=31)
        axis.text(
            0,
            1.015,
            subtitle,
            transform=axis.transAxes,
            color=MUTED,
            fontsize=11,
            va="bottom",
        )
        axis.text(
            0.025,
            0.035,
            f"Median memory → nearest-turn distance: {nearest[label]:.2f}",
            transform=axis.transAxes,
            color=INK,
            fontsize=9.5,
            fontweight="bold",
            bbox={
                "boxstyle": "square,pad=0.55",
                "facecolor": PAPER,
                "edgecolor": LINE,
                "alpha": 0.96,
            },
            zorder=5,
        )
        for spine in axis.spines.values():
            spine.set_linewidth(0.9)

    legend = [
        Line2D(
            [0],
            [0],
            color=PATH,
            marker="o",
            markerfacecolor=WHITE,
            markeredgecolor=PATH,
            linewidth=1.2,
            markersize=6,
            label="Conversation turns connected in order",
        ),
        Line2D(
            [0],
            [0],
            color="none",
            marker="o",
            markerfacecolor="none",
            markeredgecolor=ORANGE,
            markeredgewidth=1.2,
            markersize=7,
            label="Stored memories",
        ),
        Line2D(
            [0],
            [0],
            color="none",
            marker="*",
            markerfacecolor=RED,
            markeredgecolor=INK,
            markersize=10,
            label="Test question",
        ),
    ]
    figure.legend(
        handles=legend,
        loc="upper left",
        bbox_to_anchor=(0.055, 0.935),
        ncol=3,
        frameon=False,
        fontsize=10,
    )
    figure.suptitle(
        "Conversation paths and the memories around them",
        x=0.055,
        y=0.99,
        ha="left",
        fontsize=25,
        fontweight="normal",
    )
    figure.text(
        0.945,
        0.015,
        "Shared t-SNE projection · illustrative view of one LongMemEval conversation",
        ha="right",
        color=MUTED,
        fontsize=9,
    )
    figure.subplots_adjust(left=0.055, right=0.945, top=0.81, bottom=0.065, wspace=0.06)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
