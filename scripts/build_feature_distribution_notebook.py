from pathlib import Path

import nbformat as nbf


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = ROOT / "notebooks" / "01_smtlib_feature_distributions.ipynb"
NOTEBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)

nb = nbf.v4.new_notebook()
nb["metadata"] = {
    "kernelspec": {
        "display_name": "ksmt-ml (.venv)",
        "language": "python",
        "name": "python3",
    },
    "language_info": {"name": "python", "version": "3.12"},
}

nb["cells"] = [
    nbf.v4.new_markdown_cell(
        """# SMT-LIB 2025: feature distributions

This notebook analyses all benchmarks from the ten selected SMT-LIB 2025
non-incremental logic archives. One row is one `.smt2` solver input. The raw
157-position `ksmt-machsmt-inspired-v1` vector is preserved. Structural
features use `NaN` when safe AST extraction was not possible.

The goal is to understand dataset balance, feature sparsity, theory separation,
and leakage risks before training a cvc5 configuration selector."""
    ),
    nbf.v4.new_code_cell(
        """from pathlib import Path
import hashlib
import json
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from IPython.display import display
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=FutureWarning)
sns.set_theme(style="whitegrid", context="notebook")
pd.set_option("display.max_rows", 200)
pd.set_option("display.max_columns", 40)

ROOT = Path.cwd()
if ROOT.name == "notebooks":
    ROOT = ROOT.parent

DATA_PATH = ROOT / "data/processed/smtlib-2025-features.csv.gz"
SCHEMA_PATH = ROOT / "data/processed/smtlib-2025-features.schema.csv"
MANIFEST_PATH = ROOT / "data/processed/smtlib-2025-features.manifest.json"
FIGURE_DIR = ROOT / "reports/feature-distributions"
FIGURE_DIR.mkdir(parents=True, exist_ok=True)

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def finish_figure(name):
    plt.tight_layout()
    plt.savefig(FIGURE_DIR / name, dpi=160, bbox_inches="tight")
    plt.show()
"""
    ),
    nbf.v4.new_markdown_cell("## Load and validate"),
    nbf.v4.new_code_cell(
        """manifest = json.loads(MANIFEST_PATH.read_text())
assert manifest["status"] == "complete"
assert sha256_file(DATA_PATH) == manifest["outputSha256"]
assert sha256_file(SCHEMA_PATH) == manifest["schemaSha256"]

df = pd.read_csv(DATA_PATH, compression="gzip", low_memory=False)
schema = pd.read_csv(SCHEMA_PATH)
feature_columns = schema["name"].tolist()
name_by_id = schema.set_index("id")["name"].to_dict()
cluster_by_feature = schema.set_index("name")["cluster"].to_dict()

assert len(feature_columns) == 157
assert schema["schema_version"].nunique() == 1
assert schema["schema_version"].iat[0] == "ksmt-machsmt-inspired-v1"
assert set(feature_columns).issubset(df.columns)
assert df["relative_path"].is_unique
assert df["feature_schema"].nunique() == 1
assert df["feature_schema"].iat[0] == schema["schema_version"].iat[0]
assert manifest["featureSchema"] == df["feature_schema"].iat[0]
assert manifest["datasetId"] == df["dataset_id"].iat[0]
assert df["benchmark_sha256"].str.fullmatch(r"[0-9a-f]{64}").all()

provenance = pd.Series({
    "dataset_id": manifest["datasetId"],
    "feature_schema": manifest["featureSchema"],
    "code_revision": manifest["codeRevision"],
    "input_manifest_sha256": manifest["inputManifestSha256"],
    "feature_table_sha256": manifest["outputSha256"],
    "schema_sha256": manifest["schemaSha256"],
})
overview = pd.Series({
    "benchmarks": len(df),
    "logics": df["logic"].nunique(),
    "families": df[["logic", "family"]].drop_duplicates().shape[0],
    "full AST rows": (df["extraction_status"] == "ok").sum(),
    "syntax-only rows": (df["extraction_status"] == "partial").sum(),
    "failed rows": (df["extraction_status"] == "error").sum(),
})
display(provenance.to_frame("value"))
display(overview.to_frame("value"))
display(df.head(3))
"""
    ),
    nbf.v4.new_markdown_cell("## Dataset composition and extraction quality"),
    nbf.v4.new_code_cell(
        """logic_counts = df["logic"].value_counts().sort_values()
status_by_logic = pd.crosstab(df["logic"], df["extraction_status"])

fig, axes = plt.subplots(1, 2, figsize=(15, 6))
logic_counts.plot.barh(ax=axes[0], color=sns.color_palette()[0])
axes[0].set_xscale("log")
axes[0].set_xlabel("benchmarks (log scale)")
axes[0].set_ylabel("logic")
axes[0].set_title("Archive sizes are strongly imbalanced")

status_by_logic.reindex(logic_counts.index).plot.barh(stacked=True, ax=axes[1])
axes[1].set_xlabel("benchmarks")
axes[1].set_ylabel("")
axes[1].set_title("Feature extraction status")
axes[1].legend(title="status")
finish_figure("01_dataset_composition.png")

display(status_by_logic.assign(total=status_by_logic.sum(axis=1)).sort_values("total", ascending=False))
"""
    ),
    nbf.v4.new_markdown_cell("## Feature availability, variance, and sparsity"),
    nbf.v4.new_code_cell(
        """usable = df[df["extraction_status"] != "error"].copy()
X = usable[feature_columns].replace(-1.0, np.nan)

feature_stats = schema.copy()
feature_stats["available_rate"] = X.notna().mean().reindex(feature_columns).to_numpy()
feature_stats["nonzero_rate"] = X.fillna(0).ne(0).mean().reindex(feature_columns).to_numpy()
feature_stats["variance"] = X.var().reindex(feature_columns).to_numpy()
feature_stats["median"] = X.median().reindex(feature_columns).to_numpy()
feature_stats["p95"] = X.quantile(0.95).reindex(feature_columns).to_numpy()
feature_stats["max"] = X.max().reindex(feature_columns).to_numpy()

cluster_stats = feature_stats.groupby("cluster").agg(
    features=("id", "size"),
    median_available_rate=("available_rate", "median"),
    median_nonzero_rate=("nonzero_rate", "median"),
    nonconstant_features=("variance", lambda s: int((s.fillna(0) > 0).sum())),
).sort_values("median_nonzero_rate", ascending=False)
display(cluster_stats)

fig, ax = plt.subplots(figsize=(10, 6))
plot_stats = cluster_stats.sort_values("median_nonzero_rate")
ax.barh(plot_stats.index, plot_stats["median_nonzero_rate"] * 100)
ax.set_xlabel("median share of non-zero rows, %")
ax.set_ylabel("feature cluster")
ax.set_title("Most theory clusters are sparse outside their own logics")
finish_figure("02_cluster_sparsity.png")

display(feature_stats.sort_values(["nonzero_rate", "variance"], ascending=False).head(25))
"""
    ),
    nbf.v4.new_markdown_cell("## Logic signatures by feature cluster"),
    nbf.v4.new_code_cell(
        """analysis_clusters = [
    c for c in schema["cluster"].unique()
    if c not in {"source-metadata", "control-commands"}
]

cluster_activity = pd.DataFrame(index=usable.index)
for cluster in analysis_clusters:
    columns = schema.loc[schema["cluster"] == cluster, "name"]
    values = usable[columns].replace(-1.0, np.nan).clip(lower=0)
    cluster_activity[cluster] = np.log1p(values).mean(axis=1)
cluster_activity["logic"] = usable["logic"]

logic_cluster_medians = cluster_activity.groupby("logic").median()
fig, ax = plt.subplots(figsize=(14, 7))
sns.heatmap(logic_cluster_medians, cmap="mako", ax=ax, robust=True)
ax.set_xlabel("feature cluster")
ax.set_ylabel("logic")
ax.set_title("Median log-activity exposes each logic's theory signature")
finish_figure("03_logic_cluster_heatmap.png")
"""
    ),
    nbf.v4.new_markdown_cell("## Selected feature distributions"),
    nbf.v4.new_code_cell(
        """selected_ids = [2, 8, 39, 42, 47, 48, 56, 105, 133, 134, 151, 152, 157]
selected = [name_by_id[i] for i in selected_ids]
selected_medians = (
    usable.groupby("logic")[selected]
    .median()
    .clip(lower=0)
    .pipe(np.log1p)
)
selected_medians.columns = [f"f{i:03d}" for i in selected_ids]

fig, ax = plt.subplots(figsize=(14, 7))
sns.heatmap(selected_medians, cmap="rocket_r", ax=ax, robust=True, annot=False)
ax.set_xlabel("selected feature (median log1p count)")
ax.set_ylabel("logic")
ax.set_title("Compact features separate the selected theories")
finish_figure("04_selected_feature_heatmap.png")

size_feature = name_by_id[157]
fig, ax = plt.subplots(figsize=(13, 6))
size_plot = usable[["logic", size_feature]].copy()
size_plot["log10_bytes"] = np.log10(size_plot[size_feature].clip(lower=1))
sns.boxplot(data=size_plot, x="logic", y="log10_bytes", showfliers=False, ax=ax)
ax.tick_params(axis="x", rotation=45)
ax.set_xlabel("logic")
ax.set_ylabel("log10(source bytes)")
ax.set_title("Benchmark size is heavy-tailed and logic-dependent")
finish_figure("05_source_size_by_logic.png")
"""
    ),
    nbf.v4.new_markdown_cell("## Two-dimensional view of the feature space"),
    nbf.v4.new_code_cell(
        """model_clusters = {
    "declarations", "quantifiers-and-bindings", "boolean-core", "arrays",
    "bit-vectors", "floating-point", "arithmetic", "uninterpreted-functions",
}
model_features = schema.loc[schema["cluster"].isin(model_clusters), "name"].tolist()
full_ast = df[df["extraction_status"] == "ok"].copy()
sampled = pd.concat([
    group.sample(min(len(group), 1_500), random_state=42)
    for _, group in full_ast.groupby("logic")
], ignore_index=True)

Xp = sampled[model_features].replace(-1.0, np.nan).clip(lower=0).pipe(np.log1p)
variable = Xp.columns[Xp.nunique(dropna=True) > 1]
pipeline = make_pipeline(
    SimpleImputer(strategy="median"),
    StandardScaler(),
    PCA(n_components=2, random_state=42),
)
embedding = pipeline.fit_transform(Xp[variable])
pca = pipeline.named_steps["pca"]
pca_df = pd.DataFrame({"PC1": embedding[:, 0], "PC2": embedding[:, 1], "logic": sampled["logic"]})
display(pd.Series({
    "PC1 explained variance": pca.explained_variance_ratio_[0],
    "PC2 explained variance": pca.explained_variance_ratio_[1],
    "two-component total": pca.explained_variance_ratio_.sum(),
}).to_frame("ratio"))

fig, ax = plt.subplots(figsize=(12, 8))
sns.scatterplot(
    data=pca_df, x="PC1", y="PC2", hue="logic", s=13, alpha=0.5,
    linewidth=0, palette="tab10", ax=ax,
)
ax.set_title(
    f"PCA of log-scaled theory features "
    f"({pca.explained_variance_ratio_.sum():.1%} variance in 2D)"
)
ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", title="logic")
finish_figure("06_feature_pca.png")
"""
    ),
    nbf.v4.new_markdown_cell("## Family structure and leakage risk"),
    nbf.v4.new_code_cell(
        """family_counts = df.groupby(["logic", "family"]).size().rename("benchmarks").reset_index()
logic_family_summary = family_counts.groupby("logic").agg(
    families=("family", "nunique"),
    largest_family=("benchmarks", "max"),
    total=("benchmarks", "sum"),
)
logic_family_summary["largest_family_share"] = (
    logic_family_summary["largest_family"] / logic_family_summary["total"]
)
display(logic_family_summary.sort_values("total", ascending=False))

top_families = family_counts.nlargest(20, "benchmarks").sort_values("benchmarks")
fig, ax = plt.subplots(figsize=(11, 8))
labels = top_families["logic"] + " / " + top_families["family"].replace("", "<root>")
ax.barh(labels, top_families["benchmarks"])
ax.set_xscale("log")
ax.set_xlabel("benchmarks (log scale)")
ax.set_ylabel("logic / family")
ax.set_title("A few benchmark families contribute many near-related instances")
finish_figure("07_largest_families.png")
"""
    ),
    nbf.v4.new_markdown_cell("## Pilot cohort and exact-content overlap"),
    nbf.v4.new_code_cell(
        """pilot_logics = ["QF_ALIA", "QF_AUFLIA", "LIA"]
pilot = df[df["logic"].isin(pilot_logics)].copy()
pilot_family_counts = (
    pilot.groupby(["logic", "family"]).size().rename("benchmarks").reset_index()
)
pilot_summary = pilot.groupby("logic").agg(
    benchmarks=("relative_path", "size"),
    families=("family", "nunique"),
    full_ast=("extraction_status", lambda s: int((s == "ok").sum())),
    syntax_only=("extraction_status", lambda s: int((s == "partial").sum())),
    sat=("expected_status", lambda s: int((s == "sat").sum())),
    unsat=("expected_status", lambda s: int((s == "unsat").sum())),
    unknown=("expected_status", lambda s: int((s == "unknown").sum())),
)
largest_pilot_family = pilot_family_counts.groupby("logic")["benchmarks"].max()
pilot_summary["largest_family_share"] = (
    largest_pilot_family / pilot_summary["benchmarks"]
)
display(pilot_summary)

content_multiplicity = df.groupby("benchmark_sha256").size()
pilot_content_multiplicity = pilot.groupby("benchmark_sha256").size()
display(pd.Series({
    "corpus duplicate hash groups": int((content_multiplicity > 1).sum()),
    "corpus rows in duplicate hash groups": int(content_multiplicity[content_multiplicity > 1].sum()),
    "pilot duplicate hash groups": int((pilot_content_multiplicity > 1).sum()),
    "pilot rows in duplicate hash groups": int(
        pilot_content_multiplicity[pilot_content_multiplicity > 1].sum()
    ),
}).to_frame("count"))
"""
    ),
    nbf.v4.new_markdown_cell("## Redundancy and initial feature-selection diagnostics"),
    nbf.v4.new_code_cell(
        """candidate_features = schema.loc[
    ~schema["cluster"].isin({"control-commands"}), "name"
].tolist()
candidate_matrix = usable[candidate_features].replace(-1.0, np.nan)
zero_variance = candidate_matrix.columns[candidate_matrix.nunique(dropna=False) <= 1].tolist()
duplicate_mask = candidate_matrix.fillna(-999_999).T.duplicated()
duplicate_features = candidate_matrix.columns[duplicate_mask].tolist()

diagnostics = pd.Series({
    "raw features": len(feature_columns),
    "candidate features": len(candidate_features),
    "zero-variance candidates": len(zero_variance),
    "exact duplicate candidates": len(duplicate_features),
    "remaining after mechanical filters": (
        len(candidate_features) - len(set(zero_variance) | set(duplicate_features))
    ),
})
display(diagnostics.to_frame("count"))
display(pd.DataFrame({
    "zero_variance": pd.Series(zero_variance),
    "exact_duplicate": pd.Series(duplicate_features),
}))
"""
    ),
    nbf.v4.new_markdown_cell(
        """## Interpretation checklist

1. Treat each `.smt2` file as one non-incremental solver query, but treat
   `(logic, family)` as the independent grouping unit for evaluation.
2. Quantify logic and family imbalance before choosing a primary study cohort.
3. Resolve exact-content duplicates before assigning families to splits, then
   assess near-duplicate risk within the reviewed pilot cohort.
4. Use `log1p`, robust scaling, or tree models for heavy-tailed count features.
5. Fit missing-value handling, constant/duplicate removal, transforms, and
   feature selection on training folds only.
6. Inspect partial rows by logic and family. IDs 146-156 are missing after an
   AST failure and must not be silently interpreted as structural zeros.
7. This notebook describes feature geometry, not solver-selection learnability.
   Default/SBS/VBS runtime headroom is required before training a selector."""
    ),
]

nbf.write(nb, NOTEBOOK_PATH)
print(NOTEBOOK_PATH)
