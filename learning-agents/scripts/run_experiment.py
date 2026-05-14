#!/usr/bin/env python3
"""
Script to run the guidance memory reuse experiment.
This script will ask all questions from experiment.json sequentially and collect timing data.
"""

import argparse
import json
import random
import time
from datetime import datetime
from pathlib import Path

import yaml
from rich import print
from tqdm import tqdm

from learning_agent.agents.guidance import GuidanceAgent
from learning_agent.agents.interpretation import InterpretationWrapperAgent
from learning_agent.orchestrator import AgentOrchestrator
from learning_agent.logging.logger import timed_event_context
from learning_agent.core.utils import print_panel_text, print_panel, print_header, print_table
import logging
import os


logging.basicConfig(level=logging.CRITICAL)

def load_experiment_config(experiment_file: str = "experiment.json"):
    """Load experiment configuration from JSON file."""
    with open(experiment_file, "r") as f:
        return json.load(f)


def run_experiment(
    config_path: str,
    redis_url: str,
    experiment_file: str = "experiment.json",
    hide_details: bool = False,
):
    """Run the experiment with all questions."""

    # Load experiment configuration
    experiment_config = load_experiment_config(experiment_file)
    questions = experiment_config["questions"]

    # Randomize the sequence of questions
    random.shuffle(questions)

    # Load agent configuration
    with open(config_path, "r") as f:
        agent_config = yaml.safe_load(f)

    filename = agent_config.get("filename", "test_json_anonymized.json")
    delimiter = agent_config.get("delimiter", None)

    # Initialize agents
    guidance_agent = GuidanceAgent(redis_url)
    pandas_agent = AgentOrchestrator(filename, delimiter, guidance_agent)
    interpretation_agent = InterpretationWrapperAgent()

    print(
        f"[#E0B0FF]Starting experiment: {experiment_config['experiment_name']}[/#E0B0FF]"
    )
    print(f"[#E0B0FF]Total questions: {len(questions)}[/#E0B0FF]")
    print(f"[#E0B0FF]Timestamp: {datetime.now().isoformat()}[/#E0B0FF]\n")

    
    print_header("Column summary")
    print(pandas_agent.column_summary_dict)
    print_header("Stats")
    print_table(pandas_agent.stats)
    print("\n")
        
    results = []
    # Create progress bar
    pbar = tqdm(
        total=len(questions),
        desc="Running Experiment",
        unit="question",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    for i, question in enumerate(questions, 1):
        # Update progress bar description
        pbar.set_description(f"Question {i}/{len(questions)}")

            # Simulate user input like in main.py
        print_header("Data analysis mode")
        with timed_event_context("user") as log_context:
            # Simulate the user asking the question (no actual input needed)
            log_context.register_payload({"question": question})
        print(f"\n[steel_blue]User question: {question}[/steel_blue]\n")

        # Record start time
        start_time = time.time()

        try:
            # The pandas_agent.filter_data method handles the complete interactive workflow:
            # 1. Generates code
            # 2. Shows code to user
            # 3. User accepts/rejects (enter/r)
            # 4. If rejected, user provides feedback and agent retries
            # 5. Continues until user accepts or exits
            with timed_event_context("pandas_agent.filter_data") as log_context:
                result, metrics_list = pandas_agent.filter_data(question)
                log_context.register_payload({"result": result})

            # Calculate total time and tokens
            end_time = time.time()
            total_time = end_time - start_time

            # Extract metrics
            total_tokens = 0
            if isinstance(metrics_list, list):
                for metric in metrics_list:
                    if isinstance(metric, dict) and "total_tokens" in metric:
                        total_tokens += sum(metric["total_tokens"])
            elif isinstance(metrics_list, dict) and "total_tokens" in metrics_list:
                if isinstance(metrics_list["total_tokens"], list):
                    total_tokens = sum(metrics_list["total_tokens"])
                else:
                    total_tokens = metrics_list["total_tokens"]

            # Display metrics and result like in main.py
            print_panel(metrics_list, "Aggregated Metrics")
            
            print_panel(result, "Result")
            print("\n")

            

            # Get interpretation response like in main.py
            if pandas_agent.success_message is not None:
                interpretation_response = interpretation_agent.interpret(
                    question, pandas_agent.success_message.action, result
                )
            else:
                interpretation_response = interpretation_agent.interpret(
                    question, "No action was taken because the cached result was returned.", result
                )
            print_panel_text(interpretation_response, "Interpretation Agent Response")

            # Get guidance response
            if pandas_agent.success_message is not None:
                guidance_response = guidance_agent.add_guidance(
                    pandas_agent.error_messages, pandas_agent.success_message
                )
            else:
                guidance_response = "No guidance was added because the cached result was returned."
            print_panel_text(guidance_response, "Guidance Agent Response")

            result_data = {
                "question_index": i,
                "question": question,
                "total_time": total_time,
                "total_tokens": total_tokens,
                "guidance_response": guidance_response,
                "interpretation_response": interpretation_response,
                "success": True,
                "timestamp": datetime.now().isoformat(),
            }

            print(
                f"[green]✓ Success - Time: {total_time:.3f}s, Tokens: {total_tokens}[/green]"
            )

            # Update progress bar
            pbar.update(1)
            pbar.set_postfix(
                {"Time": f"{total_time:.2f}s", "Tokens": total_tokens, "Success": "✓"}
            )

        except Exception as e:
            end_time = time.time()
            total_time = end_time - start_time

            result_data = {
                "question_index": i,
                "question": question,
                "total_time": total_time,
                "total_tokens": 0,
                "guidance_response": "",
                "interpretation_response": "",
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

            print(f"[red]✗ Error - Time: {total_time:.3f}s, Error: {str(e)}[/red]\n")

            # Update progress bar for error
            pbar.update(1)
            pbar.set_postfix(
                {"Time": f"{total_time:.2f}s", "Tokens": 0, "Success": "✗"}
            )

        results.append(result_data)

    # Close progress bar
    pbar.close()

    if not os.path.exists("experiment_results"):
        os.makedirs("experiment_results")

    # Save results
    output_file = f"experiment_results/experiment_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(
            {
                "experiment_config": experiment_config,
                "results": results,
                "summary": {
                    "total_questions": len(questions),
                    "successful_questions": sum(1 for r in results if r["success"]),
                    "failed_questions": sum(1 for r in results if not r["success"]),
                    "total_time": sum(r["total_time"] for r in results),
                    "total_tokens": sum(r["total_tokens"] for r in results),
                    "average_time": sum(r["total_time"] for r in results)
                    / len(results),
                    "average_tokens": sum(r["total_tokens"] for r in results)
                    / len(results),
                },
            },
            f,
            indent=2,
        )

    print(f"[#E0B0FF]Experiment completed! Results saved to: {output_file}[/#E0B0FF]")

    # Print summary
    successful_results = [r for r in results if r["success"]]
    if successful_results:
        times = [r["total_time"] for r in successful_results]
        tokens = [r["total_tokens"] for r in successful_results]

        print(f"\n[bold]Summary:[/bold]")
        print(f"  Total questions: {len(questions)}")
        print(f"  Successful: {len(successful_results)}")
        print(f"  Failed: {len(results) - len(successful_results)}")
        print(f"  Total time: {sum(times):.3f}s")
        print(f"  Total tokens: {sum(tokens)}")
        print(f"  Average time: {sum(times)/len(times):.3f}s")
        print(f"  Average tokens: {sum(tokens)/len(tokens):.1f}")
        print(f"  Min time: {min(times):.3f}s")
        print(f"  Max time: {max(times):.3f}s")

    return output_file


def main():
    parser = argparse.ArgumentParser(description="Run guidance memory reuse experiment")
    parser.add_argument(
        "--config",
        type=str,
        default="config/bank-config.yaml",
        help="Path to the agent config file",
    )
    parser.add_argument(
        "--redis-url", type=str, default="redis://localhost:6379", help="Redis URL"
    )
    parser.add_argument(
        "--experiment-file",
        type=str,
        default="experiment.json",
        help="Path to experiment configuration file",
    )
    parser.add_argument(
        "--hide-details",
        action="store_true",
        help="Hide detailed output for each question (metrics, results, interpretations)",
    )

    args = parser.parse_args()

    # Check if experiment file exists
    if not Path(args.experiment_file).exists():
        print(f"[red]Error: Experiment file {args.experiment_file} not found![/red]")
        return

    # Run experiment
    results_file = run_experiment(
        args.config,
        args.redis_url,
        args.experiment_file,
        hide_details=args.hide_details,
    )
    print(
        f"\n[green]Experiment completed! Use analyze_latency.py to analyze the results.[/green]"
    )


if __name__ == "__main__":
    main()
