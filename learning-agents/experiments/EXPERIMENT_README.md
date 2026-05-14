# Guidance Memory Reuse Latency Experiment

This experiment is designed to validate the hypothesis that **guidance memory reuse leads to decreasing response latency** in AI agents. The experiment uses a set of carefully crafted questions with overlapping patterns to test whether the agent becomes more efficient over time.

## Hypothesis

**Primary Hypothesis**: As the agent processes questions with similar patterns, it should reuse guidance memory from previous similar questions, leading to:
1. **Decreasing response times** for similar question types
2. **Reduced token usage** as the agent learns from previous interactions
3. **Improved efficiency** through guidance memory accumulation

## Experiment Design

### Question Structure
The experiment uses 42 questions organized into 13 overlapping patterns:

1. **Education & Loan Status** (4 questions): Testing distribution analysis patterns
2. **Job & Balance** (3 questions): Testing aggregation and visualization patterns  
3. **Job & Default Rates** (3 questions): Testing percentage calculation patterns
4. **Education & Default Rates** (3 questions): Testing correlation analysis patterns
5. **Education & Lowest Default** (3 questions): Testing ranking and filtering patterns
6. **Housing & Age** (4 questions): Testing scatter plot and correlation patterns
7. **Age Density** (3 questions): Testing density plot patterns
8. **Job Top Balance** (3 questions): Testing top-N analysis patterns
9. **Job Lowest Balance** (3 questions): Testing bottom-N analysis patterns
10. **Default Education Correlation** (3 questions): Testing correlation analysis patterns
11. **Job Average Balance** (3 questions): Testing mean calculation patterns
12. **Job Marital Status** (3 questions): Testing categorical analysis patterns
13. **Campaign Distribution** (4 questions): Testing grouped distribution patterns

### Expected Patterns
Each pattern contains questions that are semantically similar but phrased differently, allowing the agent to:
- Build guidance memory for similar tasks
- Reuse learned patterns
- Demonstrate decreasing latency over time

## Files Overview

### Core Experiment Files
- `experiment.json` - Contains the 42 test questions and pattern definitions
- `run_experiment.py` - Script to execute the experiment and collect timing data
- `analyze_latency.py` - Script to analyze results and generate plots
- `experiment_notebook.py` - Convenient interface for running experiments from notebooks

### Configuration Files
- `config/config.yaml` - Agent configuration for banking dataset
- `config/insurance-config.yaml` - Agent configuration for insurance dataset

## Usage

### Quick Start (Recommended)

```python
# From a notebook or Python script
from experiment_notebook import quick_experiment_run

# Run the complete experiment
results = quick_experiment_run()

# Print summary
from experiment_notebook import print_experiment_summary
print_experiment_summary(results['results_file'])
```

### Command Line Usage

```bash
# Run the experiment
python run_experiment.py --config config/config.yaml --experiment-file experiment.json

# Analyze the results (replace with your results file)
python analyze_latency.py experiment_results_20241217_143022.json --output-dir plots
```

### Custom Experiments

```python
from experiment_notebook import create_custom_experiment

# Create your own experiment
custom_questions = [
    "Plot the distribution of age by gender",
    "Show me the age distribution for each gender",
    "What is the average age by gender?",
    # ... more questions
]

experiment_file = create_custom_experiment(
    questions=custom_questions,
    experiment_name="my_custom_experiment"
)

# Run your custom experiment
results = quick_experiment_run(experiment_file=experiment_file)
```

## Analysis Output

The analysis generates several visualizations and reports:

### Plots Generated
1. **Comprehensive Analysis** (`latency_analysis_comprehensive.png`):
   - Response time trends with moving averages
   - Token usage trends
   - Time vs tokens scatter plot
   - Pattern-based analysis
   - Cumulative time analysis
   - Time distribution histogram

2. **Pattern Analysis** (`pattern_analysis.png`):
   - Individual pattern trends
   - Pattern-specific latency analysis

3. **Summary Table** (`summary_table.png`):
   - Statistical summary for each pattern
   - Overall experiment statistics

### Data Files
- `summary_statistics.csv` - Detailed statistics in CSV format
- `experiment_results_*.json` - Raw experiment data

## Interpreting Results

### Key Metrics to Watch

1. **Overall Time Trend**: Should be negative (decreasing) if hypothesis is correct
2. **Pattern-Specific Trends**: Similar questions within patterns should show decreasing latency
3. **Token Usage Trends**: Should decrease as agent reuses guidance
4. **Moving Averages**: Should show smooth decreasing trends

### Hypothesis Validation

The analysis automatically validates the hypothesis by checking:
- ✅ Overall response time decreasing
- ✅ Overall token usage decreasing  
- ✅ Patterns with decreasing latency (count/total)

### Example Expected Results

```
Hypothesis Validation:
  ✓ Overall response time is decreasing (trend: -0.045s)
  ✓ Overall token usage is decreasing (trend: -12.3 tokens)
  Patterns with decreasing latency: 11/13
```

## Prerequisites

1. **Redis Server**: Required for guidance memory storage
   ```bash
   # Start Redis (if not already running)
   redis-server
   ```

2. **Python Dependencies**: Install required packages
   ```bash
   pip install matplotlib pandas seaborn numpy
   ```

3. **Agent Setup**: Ensure the learning agent is properly configured with:
   - Valid configuration files
   - Access to the dataset
   - OpenAI API access (for the agent)

## Troubleshooting

### Common Issues

1. **Redis Connection Error**:
   ```bash
   # Check if Redis is running
   redis-cli ping
   # Should return PONG
   ```

2. **Missing Configuration Files**:
   - Ensure `config/config.yaml` exists
   - Check dataset file paths in configuration

3. **Permission Errors**:
   ```bash
   # Make scripts executable
   chmod +x run_experiment.py analyze_latency.py
   ```

### Debug Mode

For detailed debugging, you can run individual components:

```python
# Test experiment loading
from experiment_notebook import load_experiment_config
config = load_experiment_config("experiment.json")
print(f"Loaded {len(config['questions'])} questions")

# Test analysis without running experiment
from analyze_latency import load_experiment_results, analyze_latency_trends
data = load_experiment_results("your_results_file.json")
analysis = analyze_latency_trends(data["results"], data["experiment_config"])
```

## Extending the Experiment

### Adding New Patterns

1. Add questions to `experiment.json`
2. Define pattern indices in `expected_patterns`
3. Run the experiment and analyze results

### Custom Analysis

You can extend the analysis by modifying `analyze_latency.py`:
- Add new visualization types
- Implement additional statistical tests
- Create custom pattern detection algorithms

### Different Datasets

To test with different datasets:
1. Create new configuration files
2. Adapt questions to the new dataset schema
3. Run experiments with different configs

## Contributing

When contributing to this experiment:
1. Maintain the question pattern structure
2. Ensure questions are semantically similar within patterns
3. Add appropriate documentation for new features
4. Test with different datasets and configurations

## License

This experiment is part of the AI Research Semantic Cache project. 