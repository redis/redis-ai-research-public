# Makefile for Python Project

# Variables
SRC_DIR = src/learning_agent# Directory where the source code is located
PYTHON_FILES = $(wildcard $(SRC_DIR)/**/*.py)  # All Python files in the src directory
ISORT = isort
BLACK = black
PYLINT = pylint

# Targets

# Run the main application
run:
	poetry run python $(SRC_DIR)/main.py

# Format the code using black
black:
	poetry run $(BLACK) $(SRC_DIR)

# Reorder imports using isort
isort:
	poetry run $(ISORT) $(SRC_DIR)

# Run pylint for linting
pylint:
	poetry run $(PYLINT) $(SRC_DIR)

# Run all: Format, reorder imports, and lint
all: black isort pylint

# Clean up generated Python files, e.g., .pyc files
clean:
	find . -name "*.pyc" -exec rm -f {} \;

