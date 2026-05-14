import argparse

import yaml
from rich import print
from rich.prompt import Prompt

from learning_agent.agents.guidance import GuidanceAgent
from learning_agent.agents.interpretation import InterpretationWrapperAgent
from learning_agent.logging.logger import timed_event_context
from learning_agent.orchestrator import AgentOrchestrator


def main(args):
    # The script is expected to be run from the root of the experiment directory
    # `experiments/ai-hackathon-learning-agent`
    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    filename = config.get("filename", "test_json_anonymized.json")
    delimiter = config.get("delimiter", None)
    guidance_agent = GuidanceAgent(args.redis_url)
    pandas_agent = AgentOrchestrator(filename, delimiter, guidance_agent)
    interpretation_agent = InterpretationWrapperAgent()
    print("[#E0B0FF]########## Column summary ##########[#E0B0FF]")
    print(pandas_agent.column_summary_dict)
    print("[#E0B0FF]########## Stats ##########[#E0B0FF]")
    print(pandas_agent.stats)
    while True:
        print(f"[#E0B0FF]{'=' * 30} Data analysis mode {'=' * 30}[#E0B0FF]")
        with timed_event_context("user") as log_context:
            question = Prompt.ask(
                "\n\n[steel_blue]What would you like to do with the data? (empty to exit) ====> [/steel_blue]"
            )
            log_context.register_payload({"question": question})
        print("\n")
        if question == "":
            break

        with timed_event_context("pandas_agent.filter_data") as log_context:
            result, metrics_list = pandas_agent.filter_data(question)
            log_context.register_payload({"result": result})

        print(f"[steel_blue]Metrics: {metrics_list}[/steel_blue]")
        print(f"[steel_blue]Result: {result}[/steel_blue]")
        print("\n")

        interpretation_response = interpretation_agent.interpret(
            question, pandas_agent.success_message.action, result
        )
        print(f"[bold blue]Interpretation: {interpretation_response}[/bold blue]")

        guidance_response = guidance_agent.add_guidance(
            pandas_agent.error_messages, pandas_agent.success_message
        )
        print(
            f"[#E0B0FF]Guidance agent response:[#E0B0FF] [steel_blue] `{guidance_response}` [/steel_blue]\n\n"
        )


def run():
    parser = argparse.ArgumentParser()
    # As per comment in script, it is expected to be run from `experiments/ai-hackathon-learning-agent`
    parser.add_argument(
        "--config",
        type=str,
        default="config/bank-config.yaml",
        help="Path to the config file",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default="redis://localhost:6379",
        help="Redis URL",
    )
    args = parser.parse_args()
    main(args)


if __name__ == "__main__":
    run()
# I need to know the average etel by session id
# Distribution of etel
# plot a bar chart of the mean etel value by session
# What jobs have the highest rate of default?
# Plot average default rates for each education tier
# Metrics: {'total_tokens': [637], 'time': [1.7681754999794066]}
# Which education degree is least likely to default
# Plot the distribution of balance by job category
# Give me a breakdown of the percentage of married people by age groups of 10 years
# Metrics: [{'total_tokens': [985], 'time': [7.737273167120293]}, {'total_tokens': [1189], 'time': [2.0619303749408573]}, {'total_tokens':
# [1197], 'time': [5.897580499993637]}, {'total_tokens': [1185], 'time': [2.98735254118219]}, {'total_tokens': [917], 'time':
# [2.252619333099574]}]
# Second run is Metrics: [{'total_tokens': [779], 'time': [2.4558155410923064]}]
# Third run is [{'total_tokens': [504], 'time': [0.9079972920008004]}]
# Fourth run is [{'total_tokens': [748], 'time': [2.187650917097926]}]
# restart run is [{'total_tokens': [759], 'time': [2.449421958066523]}]
# Total tokens for first run is 985 + 1189 + 1197 + 1185 + 917 = 5473
# Total time for first run is 20.94s

# What is the distribution of duration?
# Metrics: [{'total_tokens': [448], 'time': [0.5102728751953691]}, {'total_tokens': [490], 'time': [0.4663409579079598]}]
# Metrics: {'total_tokens': [502], 'time': [0.48684025020338595]}
