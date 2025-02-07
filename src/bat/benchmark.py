import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

import numpy as np
import json


def get_nice_benchmark_name(bench_name):
    with open(
        os.path.join(Path(__file__).parent, "assets/prettified_bencmark_names.json"),
        "r",
    ) as f:
        prettified_names = json.load(f)

    if bench_name in prettified_names:
        return prettified_names[bench_name]
    else:
        return bench_name


def lower_os_better_for_source(source_name):
    with open(
        os.path.join(Path(__file__).parent, "assets/lower_is_better_benchmarks.txt"),
        "r",
    ) as f:
        lower_is_better_sources = [
            source.replace("\n", "") + ".csv" for source in f.readlines()
        ]

    return source_name in lower_is_better_sources


class Benchmark:
    def __init__(self, df=pd.DataFrame(), data_source=None, normalized_names=False):
        self.is_empty = True
        self.df = None
        if len(df) > 0:
            assert (
                data_source or "source" in df.columns
            ), "A datasource must be inputted with a df"
            self.validate_df_pre_formatting(df)
            self.assign_df(df, data_source, normalized_names=normalized_names)

    def load_local_catalog(self, catalog_rel_path="assets/benchmarks"):
        catalog_path = os.path.join(Path(__file__).parent, catalog_rel_path)

        for file_name in os.listdir(catalog_path):
            self.extend(
                Benchmark(
                    pd.read_csv(os.path.join(catalog_path, file_name)),
                    data_source=file_name,
                )
            )

    def assign_df(self, df, data_source, normalized_names, lower_is_better=False):
        assert (
            df.columns[0] == "model"
        ), f'the zeroth df column mush be "model", instead, got {df.columns[0]}'

        if "scenario" not in df.columns:
            # Assuming the first column is 'model' and the rest are scenarios
            df = pd.melt(df, id_vars=["model"], var_name="scenario", value_name="score")

        df.replace("-", np.nan, inplace=True)
        df.dropna()
        df["score"] = df["score"].astype(float, errors="ignore")

        if not normalized_names:
            df["model"] = df["model"].apply(self.standardize_model_name)
            df["scenario"] = df["scenario"].apply(self.standardize_scenario_name)
        df["aggragated_from"] = [[] for _ in range(len(df))]
        if data_source:
            df["source"] = data_source
        self.df = df
        self.validate_dataframe_post_formatting()
        self.df.dropna(inplace=True)

        if lower_os_better_for_source(data_source):
            self.normalize_scores_per_scenario(lower_is_better=True)

        self.is_empty = False

    def normalize_scores_per_scenario(self, lower_is_better=False):
        """
        Normalize the 'score' column in the DataFrame to a 0-1 range within each scenario.

        Parameters:
        df (pd.DataFrame): DataFrame containing 'scenario', 'model', and 'score' columns.

        Returns:
        pd.DataFrame: DataFrame with the 'score' column normalized within each scenario.
        """
        if "score" not in self.df.columns:
            raise ValueError("DataFrame must contain a 'score' column")

        # Apply normalization within each group defined by 'scenario'
        def normalize(group):
            min_score = group["score"].min()
            max_score = group["score"].max()
            # Avoid division by zero in case all scores in a group are the same
            if max_score == min_score:
                group["score"] = (
                    1  # or 0, depending on how you want to handle this case
                )
            else:
                group["score"] = (group["score"] - min_score) / (max_score - min_score)
            return group

        self.df = self.df.groupby("scenario", as_index=False, group_keys=False).apply(
            normalize
        )
        if lower_is_better:
            self.df["score"] = 1 - self.df["score"]

    def add_aggregate(
        self,
        new_col_name,
        scenario_blacklist=[],
        scenario_whitelist=[],
        mean_or_mwr="mwr",
        agg_source_name=None,
        min_scenario_for_models_to_appear_in_agg=0,
    ):
        def calculate_win_rate(series):
            assert (
                len(series) > 1
            ), "Error: tryting to get the mean win rate with only one column"

            def win_rate(x):
                win_count = sum(1 for value in series if x > value)
                return win_count / (len(series) - 1)

            return series.transform(win_rate)

        assert not (
            scenario_blacklist and scenario_whitelist
        ), "either scenario_blacklist or scenario_whitelist can be inputted, but not both"
        if scenario_blacklist:
            df_for_agg = self.df.query("scenario not in @scenario_blacklist")
        elif scenario_whitelist:
            df_for_agg = self.df.query("scenario in @scenario_whitelist")
        else:
            pass  # all scenarios are just used here

        n_scenario_for_aggregate = len(df_for_agg["scenario"].unique())
        min_scenario_for_models_to_appear_in_agg = min(
            min_scenario_for_models_to_appear_in_agg, n_scenario_for_aggregate
        )

        # remove models that appears in less then
        models_to_consider = (  # noqa: F841
            df_for_agg.groupby(["model"])["scenario"]
            .count()
            .to_frame()
            .query("scenario>=@min_scenario_for_models_to_appear_in_agg")
            .index.to_list()
        )

        df_for_agg = df_for_agg.query("model in @models_to_consider").copy()

        df_for_agg["wr"] = df_for_agg.groupby(["scenario"])["score"].transform(
            calculate_win_rate
        )

        mean_df = (
            df_for_agg.groupby(["model"])
            .agg({"score": "mean", "wr": "mean"})
            .reset_index()
        )
        mean_df["score"] = mean_df["wr"] if mean_or_mwr == "mwr" else mean_df["score"]
        mean_df["scenario"] = new_col_name
        mean_df["aggragated_from"] = mean_df["scenario"].apply(
            lambda x: [
                scenario
                for scenario in self.df["scenario"].unique()
                if scenario not in scenario_blacklist
            ]
        )

        if agg_source_name:
            mean_df["source"] = agg_source_name
        elif len(self.df["source"].unique()) == 1:
            mean_df["source"] = self.df["source"].unique()[0]
        else:
            raise IOError(
                "more that one source for aggrageted column, in this case, you must specify a agg_source_name"
            )

        self.df = pd.concat([self.df, mean_df.drop(columns=["wr"])])

    def validate_df_pre_formatting(self, df):
        """
        Validate the input DataFrame before formatting.
        """
        if "Unnamed: 0" in df.columns:
            raise ValueError("DataFrame should not contain 'Unnamed: 0' column")

        # Basic column checks
        if "model" not in df.columns:
            raise ValueError("DataFrame must contain a 'model' column")
        if "scenario" not in df.columns and len(df.columns) < 2:
            raise ValueError(
                "DataFrame must contain at least 'model' and one scenario column or 'scenario' and 'score' column"
            )

        # # Check for duplicate model-scenario pairs (before melting)
        # if "scenario" not in df.columns:
        #     melted_df = pd.melt(
        #         df, id_vars=["model"], var_name="scenario", value_name="score"
        #     )
        #     if (
        #         not len(
        #             melted_df[
        #                 melted_df.duplicated(subset=["model", "scenario"], keep=False)
        #             ]
        #         )
        #         == 0
        #     ):
        #         raise ValueError("DataFrame contains duplicate model-scenario pairs")

        # Check if scores are numeric (if the score column exists)
        if "score" in df.columns:
            if not pd.api.types.is_numeric_dtype(df["score"]):
                raise ValueError("score must be numeric")

    def validate_dataframe_post_formatting(self):
        if "Unnamed: 0" in self.df.columns:
            self.df.drop(columns=["Unnamed: 0"], inplace=True)

        required_columns = [
            "model",
            "scenario",
            "score",
            "source",
            "aggragated_from",
        ]

        relevant_columns = [
            col_name for col_name in self.df.columns.tolist() if col_name != "tag"
        ]
        if sorted(relevant_columns) != sorted(required_columns):
            raise ValueError(
                f"DataFrame must contain the following columns: {sorted(required_columns)}\n"
                f"Instead, it contains {sorted(relevant_columns)}"
            )

        if (
            not len(
                self.df[
                    self.df.duplicated(
                        subset=["model", "scenario", "source"], keep=False
                    )
                ]
            )
            == 0
        ):
            # raise ValueError("a model appears more than once for a single scenario")
            # Group by the columns you want to check for duplicates and keep the row with the highest score
            self.df = self.df.groupby(["model", "scenario", "source"], as_index=False)[
                "score"
            ].max()
            print(
                "Warning: Duplicate entries found. Keeping rows with the best scores."
            )

        if not pd.api.types.is_numeric_dtype(self.df["score"]):
            raise ValueError("score must be numeric")

    @staticmethod
    def standardize_scenario_name(name):
        name = (
            name.strip()
            .lower()
            .replace("   ", "-")
            .replace("  ", "-")
            .replace(" ", "-")
            .replace("(", "")
            .replace(")", "")
            .replace("gsm-8k", "gsm8k")
            .replace("open-book", "open")
            .replace("closed-book", "closed")
            .replace("agi-eval", "agieval")
            .replace("alpacaeval2-wr", "alpacav2")
            .replace("alpacav2,-len-adj", "alpacaeval2-lc")
            .replace("hswag", "hellaswag")
            .replace("obqa", "openbookqa")
            .replace("winogrande", "winog")
            .replace("winog", "winogrande")
            .replace("-", "_")
        )

        return get_nice_benchmark_name(name)

    @staticmethod
    def standardize_model_name(name):
        name = (
            name.strip()
            .lower()
            .replace("   ", "-")
            .replace("  ", "-")
            .replace(" ", "-")
            .replace("(", "")
            .replace(")", "")
            .replace("β", "beta")
            .replace("command-r+", "command-r-plus")
            .replace("dbrx-inst", "dbrx-instruct")
            .replace("-hf", "")
            .replace("-", "_")
            .replace("llama_3", "llama3")
            .replace("ul2", "flan-ul2")
            .split("/")[-1]
            .replace("meta_", "")
            .replace(".", "_")
            .replace("v01", "v0_1")
            .replace("v02", "v0_2")
            .replace("v03", "v0_3")
            .replace("wml/", "")
        )
        return name

    def extend(self, other):
        if not isinstance(other, Benchmark):
            raise TypeError("The added object must be an instance of Benchmark")

        if self.df is not None:
            self.df = pd.concat([self.df, other.df])
        else:
            self.df = other.df

        return self

    def get_models(self):
        return self.df["model"].unique()

    def get_scenarios(self):
        return self.df["scenario"].unique()

    def get_model_appearences_count(self):
        return (
            self.df.groupby("model")["scenario"]
            .count()
            .sort_values(ascending=False)
            .to_dict()
        )

    def get_scenario_appearences_count(self):
        return (
            self.df.groupby("scenario")["model"]
            .count()
            .sort_values(ascending=False)
            .to_dict()
        )

    def show_overlapping_model_counts(self):
        # Counting the occurrences of models for each scenario pair
        cross_tab = pd.crosstab(self.df["scenario"], self.df["model"])

        # Compute the number of models shared between each pair of scenarios
        scenario_combinations = cross_tab.dot(cross_tab.T)

        # Sorting the scenarios based on total models
        sorted_scenarios = (
            scenario_combinations.sum(axis=1).sort_values(ascending=False).index
        )
        scenario_combinations = scenario_combinations.loc[
            sorted_scenarios, sorted_scenarios
        ]

        # Plotting the heatmap
        # plt.figure(figsize=(10, 8))
        sns.clustermap(
            scenario_combinations,
            cmap="coolwarm",
            vmax=20,
            linewidths=0.002,
            xticklabels=True,
            yticklabels=True,
            fmt="d",
            annot=True,
        )

        plt.title("Heatmap of Model Count for Each Pair of Scenarios")
        plt.tight_layout()
        save_path = "figures/show_overlapping_model_counts.png"
        plt.savefig(save_path)
        plt.clf()
        print(f"saved to: {save_path}")

    def clear_repeated_scenarios(self, source_to_keep=None):
        self.df["scenario__source"] = self.df["scenario"] + "__" + self.df["source"]
        # Counting the occurrences of models for each scenario pair
        cross_tab = pd.crosstab(self.df["scenario__source"], self.df["model"])

        # Compute the number of models shared between each pair of scenarios
        scenario_combinations = cross_tab.dot(cross_tab.T)

        self.df["scenario__source_counts"] = self.df["scenario__source"].apply(
            lambda x: scenario_combinations.sum(axis=1)[x]
        )

        # scenario_counts = self.df.drop_duplicates(['scenario','source']).groupby(['scenario'])['source'].count()
        scenarios_already_delt_with = []
        scenarios_source_to_drop = []
        for scenario, scenario_df in self.df.drop_duplicates(
            ["scenario", "source"]
        ).groupby("scenario"):
            if scenario in scenarios_already_delt_with:
                continue
            # scenario = scenario[0]

            if len(scenario_df) > 1:
                if source_to_keep and source_to_keep in scenario_df["source"]:
                    scenario_source_to_keep = scenario_df.query(
                        "source!=@source_to_keep"
                    )["scenario__source"]
                else:
                    scenario_source_to_keep = scenario_df.iloc[
                        scenario_df["scenario__source_counts"].argmax()
                    ]["scenario__source"]

                cur_scenarios_source_to_drop = [
                    scen_source
                    for scen_source in scenario_df["scenario__source"].unique().tolist()
                    if scen_source not in scenario_source_to_keep
                ]
                scenarios_source_to_drop.extend(cur_scenarios_source_to_drop)
                print(
                    f"kept: {scenario_source_to_keep}, dropped: {cur_scenarios_source_to_drop}"
                )
                scenarios_already_delt_with.append(scenario)

        self.df = self.df.query("scenario__source not in @scenarios_source_to_drop")
        self.df.drop(
            columns=["scenario__source", "scenario__source_counts"], inplace=True
        )


if __name__ == "__main__":
    b = Benchmark()
    b.load_local_catalog()
    print()
