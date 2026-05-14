#!/usr/bin/env python3
"""
Script to analyze experiment results and plot latency trends.
This script will help validate the hypothesis that guidance memory reuse leads to decreasing response latency.
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def load_experiment_results(results_file: str) -> Dict:
    """Load experiment results from JSON file."""
    with open(results_file, "r") as f:
        return json.load(f)


def analyze_latency_trends(results: List[Dict], experiment_config: Dict) -> Dict:
    """Analyze latency trends and identify patterns."""

    # Extract successful results
    successful_results = [r for r in results if r["success"]]

    if not successful_results:
        return {"error": "No successful results to analyze"}

    # Create DataFrame for analysis
    df = pd.DataFrame(successful_results)
    df["question_index"] = df["question_index"].astype(int)
    df = df.sort_values("question_index")

    # Calculate moving averages
    df["time_moving_avg_3"] = df["total_time"].rolling(window=3, min_periods=1).mean()
    df["time_moving_avg_5"] = df["total_time"].rolling(window=5, min_periods=1).mean()
    df["tokens_moving_avg_3"] = (
        df["total_tokens"].rolling(window=3, min_periods=1).mean()
    )
    df["tokens_moving_avg_5"] = (
        df["total_tokens"].rolling(window=5, min_periods=1).mean()
    )

    # Identify question patterns from experiment config
    patterns = experiment_config.get("expected_patterns", {})

    # Group questions by pattern
    pattern_groups = {}
    for pattern_name, question_indices in patterns.items():
        pattern_groups[pattern_name] = []
        for idx in question_indices:
            if idx < len(df):
                pattern_groups[pattern_name].append(df.iloc[idx])

    # Calculate statistics for each pattern
    pattern_stats = {}
    for pattern_name, group_data in pattern_groups.items():
        if group_data:
            group_df = pd.DataFrame(group_data)
            pattern_stats[pattern_name] = {
                "count": len(group_df),
                "avg_time": group_df["total_time"].mean(),
                "avg_tokens": group_df["total_tokens"].mean(),
                "time_std": group_df["total_time"].std(),
                "tokens_std": group_df["total_tokens"].std(),
                "time_trend": group_df["total_time"].iloc[-1]
                - group_df["total_time"].iloc[0],
                "tokens_trend": group_df["total_tokens"].iloc[-1]
                - group_df["total_tokens"].iloc[0],
            }

    # Overall statistics
    overall_stats = {
        "total_questions": len(df),
        "avg_time": df["total_time"].mean(),
        "avg_tokens": df["total_tokens"].mean(),
        "time_std": df["total_time"].std(),
        "tokens_std": df["total_tokens"].std(),
        "time_trend": df["total_time"].iloc[-1] - df["total_time"].iloc[0],
        "tokens_trend": df["total_tokens"].iloc[-1] - df["total_tokens"].iloc[0],
        "correlation_time_tokens": df["total_time"].corr(df["total_tokens"]),
    }

    return {
        "dataframe": df,
        "pattern_stats": pattern_stats,
        "overall_stats": overall_stats,
        "patterns": patterns,
    }


def plot_latency_analysis(analysis_results: Dict, output_dir: str = "plots"):
    """Create comprehensive latency analysis plots."""

    if "error" in analysis_results:
        print(f"Error in analysis: {analysis_results['error']}")
        return

    df = analysis_results["dataframe"]
    pattern_stats = analysis_results["pattern_stats"]
    overall_stats = analysis_results["overall_stats"]
    patterns = analysis_results["patterns"]

    # Create output directory
    Path(output_dir).mkdir(exist_ok=True)

    # Set up the plotting style
    plt.style.use("default")
    sns.set_palette("husl")

    # Create a comprehensive figure with multiple subplots
    fig = plt.figure(figsize=(20, 16))

    # 1. Overall latency trend
    ax1 = plt.subplot(3, 2, 1)
    ax1.plot(
        df["question_index"], df["total_time"], "o-", alpha=0.7, label="Response Time"
    )
    ax1.plot(
        df["question_index"],
        df["time_moving_avg_3"],
        "r-",
        linewidth=2,
        label="3-Question Moving Avg",
    )
    ax1.plot(
        df["question_index"],
        df["time_moving_avg_5"],
        "g-",
        linewidth=2,
        label="5-Question Moving Avg",
    )
    ax1.set_xlabel("Question Index")
    ax1.set_ylabel("Response Time (seconds)")
    ax1.set_title("Response Time Trend Over Questions")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Add trend line
    z = np.polyfit(df["question_index"], df["total_time"], 1)
    p = np.poly1d(z)
    ax1.plot(
        df["question_index"],
        p(df["question_index"]),
        "b--",
        alpha=0.8,
        label=f"Trend: {z[0]:.4f}x + {z[1]:.2f}",
    )
    ax1.legend()

    # 2. Token usage trend
    ax2 = plt.subplot(3, 2, 2)
    ax2.plot(
        df["question_index"], df["total_tokens"], "o-", alpha=0.7, label="Token Usage"
    )
    ax2.plot(
        df["question_index"],
        df["tokens_moving_avg_3"],
        "r-",
        linewidth=2,
        label="3-Question Moving Avg",
    )
    ax2.plot(
        df["question_index"],
        df["tokens_moving_avg_5"],
        "g-",
        linewidth=2,
        label="5-Question Moving Avg",
    )
    ax2.set_xlabel("Question Index")
    ax2.set_ylabel("Total Tokens")
    ax2.set_title("Token Usage Trend Over Questions")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # Add trend line
    z_tokens = np.polyfit(df["question_index"], df["total_tokens"], 1)
    p_tokens = np.poly1d(z_tokens)
    ax2.plot(
        df["question_index"],
        p_tokens(df["question_index"]),
        "b--",
        alpha=0.8,
        label=f"Trend: {z_tokens[0]:.2f}x + {z_tokens[1]:.1f}",
    )
    ax2.legend()

    # 3. Time vs Tokens scatter plot
    ax3 = plt.subplot(3, 2, 3)
    scatter = ax3.scatter(
        df["question_index"],
        df["total_time"],
        c=df["total_tokens"],
        cmap="viridis",
        alpha=0.7,
        s=50,
    )
    ax3.set_xlabel("Question Index")
    ax3.set_ylabel("Response Time (seconds)")
    ax3.set_title("Response Time vs Question Index (colored by tokens)")
    plt.colorbar(scatter, ax=ax3, label="Total Tokens")
    ax3.grid(True, alpha=0.3)

    # 4. Pattern-based analysis
    ax4 = plt.subplot(3, 2, 4)
    if pattern_stats:
        pattern_names = list(pattern_stats.keys())
        avg_times = [pattern_stats[p]["avg_time"] for p in pattern_names]
        time_stds = [pattern_stats[p]["time_std"] for p in pattern_names]

        bars = ax4.bar(
            range(len(pattern_names)), avg_times, yerr=time_stds, capsize=5, alpha=0.7
        )
        ax4.set_xlabel("Question Pattern")
        ax4.set_ylabel("Average Response Time (seconds)")
        ax4.set_title("Average Response Time by Question Pattern")
        ax4.set_xticks(range(len(pattern_names)))
        ax4.set_xticklabels(
            [p.replace("_", "\n") for p in pattern_names], rotation=45, ha="right"
        )
        ax4.grid(True, alpha=0.3)

    # 5. Cumulative time analysis
    ax5 = plt.subplot(3, 2, 5)
    cumulative_time = df["total_time"].cumsum()
    ax5.plot(df["question_index"], cumulative_time, "o-", linewidth=2, markersize=4)
    ax5.set_xlabel("Question Index")
    ax5.set_ylabel("Cumulative Time (seconds)")
    ax5.set_title("Cumulative Response Time")
    ax5.grid(True, alpha=0.3)

    # 6. Time distribution histogram
    ax6 = plt.subplot(3, 2, 6)
    ax6.hist(df["total_time"], bins=20, alpha=0.7, edgecolor="black")
    ax6.axvline(
        df["total_time"].mean(),
        color="red",
        linestyle="--",
        label=f'Mean: {df["total_time"].mean():.3f}s',
    )
    ax6.axvline(
        df["total_time"].median(),
        color="green",
        linestyle="--",
        label=f'Median: {df["total_time"].median():.3f}s',
    )
    ax6.set_xlabel("Response Time (seconds)")
    ax6.set_ylabel("Frequency")
    ax6.set_title("Response Time Distribution")
    ax6.legend()
    ax6.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(
        f"{output_dir}/latency_analysis_comprehensive.png", dpi=300, bbox_inches="tight"
    )
    plt.close()

    # Create pattern-specific plots
    if pattern_stats:
        create_pattern_plots(df, patterns, pattern_stats, output_dir)

    # Create summary statistics table
    create_summary_table(overall_stats, pattern_stats, output_dir)

    print(f"Plots saved to {output_dir}/ directory")


def create_pattern_plots(
    df: pd.DataFrame, patterns: Dict, pattern_stats: Dict, output_dir: str
):
    """Create detailed plots for each question pattern."""

    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    axes = axes.flatten()

    for i, (pattern_name, question_indices) in enumerate(patterns.items()):
        if i >= 4:  # Limit to 4 patterns per figure
            break

        ax = axes[i]
        pattern_data = df[
            df["question_index"].isin([idx + 1 for idx in question_indices])
        ]

        if len(pattern_data) > 0:
            ax.plot(
                pattern_data["question_index"],
                pattern_data["total_time"],
                "o-",
                linewidth=2,
                markersize=6,
            )
            ax.set_xlabel("Question Index")
            ax.set_ylabel("Response Time (seconds)")
            ax.set_title(
                f'{pattern_name.replace("_", " ").title()}\nAvg: {pattern_stats[pattern_name]["avg_time"]:.3f}s'
            )
            ax.grid(True, alpha=0.3)

            # Add trend line
            if len(pattern_data) > 1:
                z = np.polyfit(
                    pattern_data["question_index"], pattern_data["total_time"], 1
                )
                p = np.poly1d(z)
                ax.plot(
                    pattern_data["question_index"],
                    p(pattern_data["question_index"]),
                    "r--",
                    alpha=0.8,
                )

    plt.tight_layout()
    plt.savefig(f"{output_dir}/pattern_analysis.png", dpi=300, bbox_inches="tight")
    plt.close()


def create_summary_table(overall_stats: Dict, pattern_stats: Dict, output_dir: str):
    """Create a summary statistics table."""

    # Create summary DataFrame
    summary_data = []

    # Overall stats
    summary_data.append(
        {
            "Pattern": "OVERALL",
            "Count": overall_stats["total_questions"],
            "Avg Time (s)": f"{overall_stats['avg_time']:.3f}",
            "Avg Tokens": f"{overall_stats['avg_tokens']:.1f}",
            "Time Std": f"{overall_stats['time_std']:.3f}",
            "Tokens Std": f"{overall_stats['tokens_std']:.1f}",
            "Time Trend": f"{overall_stats['time_trend']:+.3f}",
            "Tokens Trend": f"{overall_stats['tokens_trend']:+.1f}",
        }
    )

    # Pattern stats
    for pattern_name, stats in pattern_stats.items():
        summary_data.append(
            {
                "Pattern": pattern_name.replace("_", " ").title(),
                "Count": stats["count"],
                "Avg Time (s)": f"{stats['avg_time']:.3f}",
                "Avg Tokens": f"{stats['avg_tokens']:.1f}",
                "Time Std": f"{stats['time_std']:.3f}",
                "Tokens Std": f"{stats['tokens_std']:.1f}",
                "Time Trend": f"{stats['time_trend']:+.3f}",
                "Tokens Trend": f"{stats['tokens_trend']:+.1f}",
            }
        )

    summary_df = pd.DataFrame(summary_data)

    # Save as CSV
    summary_df.to_csv(f"{output_dir}/summary_statistics.csv", index=False)

    # Create a nice formatted table
    fig, ax = plt.subplots(figsize=(12, len(summary_data) * 0.4 + 1))
    ax.axis("tight")
    ax.axis("off")

    table = ax.table(
        cellText=summary_df.values,
        colLabels=summary_df.columns,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)

    # Color header
    for i in range(len(summary_df.columns)):
        table[(0, i)].set_facecolor("#4CAF50")
        table[(0, i)].set_text_props(weight="bold", color="white")

    # Color overall row
    for i in range(len(summary_df.columns)):
        table[(1, i)].set_facecolor("#FFC107")
        table[(1, i)].set_text_props(weight="bold")

    plt.title("Latency Analysis Summary", fontsize=16, fontweight="bold", pad=20)
    plt.savefig(f"{output_dir}/summary_table.png", dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Summary statistics saved to {output_dir}/summary_statistics.csv")


def print_analysis_summary(analysis_results: Dict):
    """Print a summary of the analysis results."""

    if "error" in analysis_results:
        print(f"Error: {analysis_results['error']}")
        return

    overall_stats = analysis_results["overall_stats"]
    pattern_stats = analysis_results["pattern_stats"]

    print("\n" + "=" * 60)
    print("LATENCY ANALYSIS SUMMARY")
    print("=" * 60)

    print(f"\nOverall Statistics:")
    print(f"  Total Questions: {overall_stats['total_questions']}")
    print(f"  Average Response Time: {overall_stats['avg_time']:.3f} seconds")
    print(f"  Average Token Usage: {overall_stats['avg_tokens']:.1f} tokens")
    print(f"  Time Standard Deviation: {overall_stats['time_std']:.3f} seconds")
    print(f"  Token Standard Deviation: {overall_stats['tokens_std']:.1f} tokens")
    print(f"  Overall Time Trend: {overall_stats['time_trend']:+.3f} seconds")
    print(f"  Overall Token Trend: {overall_stats['tokens_trend']:+.1f} tokens")
    print(f"  Time-Token Correlation: {overall_stats['correlation_time_tokens']:.3f}")

    if pattern_stats:
        print(f"\nPattern Analysis:")
        for pattern_name, stats in pattern_stats.items():
            print(f"  {pattern_name.replace('_', ' ').title()}:")
            print(f"    Count: {stats['count']}")
            print(f"    Avg Time: {stats['avg_time']:.3f}s")
            print(f"    Avg Tokens: {stats['avg_tokens']:.1f}")
            print(f"    Time Trend: {stats['time_trend']:+.3f}s")
            print(f"    Token Trend: {stats['tokens_trend']:+.1f}")

    # Hypothesis validation
    print(f"\nHypothesis Validation:")
    if overall_stats["time_trend"] < 0:
        print(
            f"  ✓ Overall response time is decreasing (trend: {overall_stats['time_trend']:+.3f}s)"
        )
    else:
        print(
            f"  ✗ Overall response time is not decreasing (trend: {overall_stats['time_trend']:+.3f}s)"
        )

    if overall_stats["tokens_trend"] < 0:
        print(
            f"  ✓ Overall token usage is decreasing (trend: {overall_stats['tokens_trend']:+.1f} tokens)"
        )
    else:
        print(
            f"  ✗ Overall token usage is not decreasing (trend: {overall_stats['tokens_trend']:+.1f} tokens)"
        )

    # Count patterns with decreasing latency
    decreasing_patterns = sum(
        1 for stats in pattern_stats.values() if stats["time_trend"] < 0
    )
    total_patterns = len(pattern_stats)
    print(f"  Patterns with decreasing latency: {decreasing_patterns}/{total_patterns}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze experiment results and plot latency trends"
    )
    parser.add_argument(
        "results_file", type=str, help="Path to experiment results JSON file"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="plots",
        help="Directory to save plots (default: plots)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip generating plots, only print summary",
    )

    args = parser.parse_args()

    # Check if results file exists
    if not Path(args.results_file).exists():
        print(f"Error: Results file {args.results_file} not found!")
        return

    # Load and analyze results
    print(f"Loading results from {args.results_file}...")
    data = load_experiment_results(args.results_file)

    results = data["results"]
    experiment_config = data["experiment_config"]

    print(f"Analyzing {len(results)} results...")
    analysis_results = analyze_latency_trends(results, experiment_config)

    # Print summary
    print_analysis_summary(analysis_results)

    # Generate plots
    if not args.no_plots:
        print(f"\nGenerating plots...")
        plot_latency_analysis(analysis_results, args.output_dir)

    print(f"\nAnalysis complete!")


if __name__ == "__main__":
    main()
