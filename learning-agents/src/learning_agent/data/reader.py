import ast

import pandas as pd
from rich import print


class RawDataReaderProcessor:
    def __init__(self, filename, delimiter=None):
        self.filename = filename
        self.delimiter = delimiter

    def get_df(self) -> pd.DataFrame:
        if self.filename.endswith(".csv"):
            if self.delimiter is None:
                df = pd.read_csv(self.filename)
            else:
                df = pd.read_csv(self.filename, sep=self.delimiter)
            return df
        if self.filename.endswith(".json"):
            content = self.read_data()
            content = self.clean_data(content)
            df = self.parse_data(content)
            return df
        raise ValueError(f"Unsupported file type: {self.filename}")

    def read_data(self) -> str:
        with open(self.filename, "r") as f:
            return f.read()

    def clean_data(self, content: str) -> str:
        # Remove trailing newline if present
        return content.strip()

    def parse_data(self, content: str) -> pd.DataFrame:
        # Safely evaluate the list structure
        try:
            data = ast.literal_eval(content)
            # print(self.data[0])  # list of [id, dict] pairs
            rows = [elem[1] for elem in data]
            df = pd.DataFrame(rows)
            print("\n\n[#E0B0FF]########## Loaded dataframe ##########[#E0B0FF]")
            print(df)
            print("\n\n")
        except Exception as e:
            print(f"Failed to parse: {e}")
        return df
