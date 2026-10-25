"""Download pinned research sources and extract a real, explicitly partial circuit."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "91bdd1e7dcf193f3e7ca5a8933497fcef63b7960"
REPOSITORY = "https://github.com/philshiu/Drosophila_brain_model"
BASE = f"https://raw.githubusercontent.com/philshiu/Drosophila_brain_model/{COMMIT}/"
FILES = [
    "2023_03_23_completeness_630_final.csv",
    "2023_03_23_connectivity_630_final.parquet",
    "model.py", "example.ipynb", "Readme.md", "LICENSE",
]
SUGAR_IDS = [
    "720575940624963786", "720575940630233916", "720575940637568838",
    "720575940638202345", "720575940617000768", "720575940630797113",
    "720575940632889389", "720575940621754367", "720575940621502051",
    "720575940640649691", "720575940639332736", "720575940616885538",
    "720575940639198653", "720575940620900446", "720575940617937543",
    "720575940632425919", "720575940633143833", "720575940612670570",
    "720575940628853239", "720575940629176663", "720575940611875570",
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(path: Path, url: str) -> None:
    if path.exists() and path.stat().st_size:
        return
    print(f"Downloading {path.name}", flush=True)
    partial = path.with_suffix(path.suffix + ".part")
    req = Request(url, headers={"User-Agent": "CoFlorist-hackathon-research"})
    with urlopen(req, timeout=90) as response, partial.open("wb") as f:
        for block in iter(lambda: response.read(1024 * 1024), b""):
            f.write(block)
    partial.replace(path)


def prepare(output: Path, count: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    source = output / "source"
    source.mkdir(exist_ok=True)
    sources = []
    for name in FILES:
        path = source / name
        download(path, BASE + name)
        sources.append({"file": name, "url": BASE + name, "bytes": path.stat().st_size,
                        "sha256": digest(path)})

    comp = pd.read_csv(source / FILES[0], index_col=0)
    con = pd.read_parquet(source / FILES[1])
    ids = [str(i) for i in comp.index]
    id_to_original = {fly_id: i for i, fly_id in enumerate(ids)}
    seed_indices = [id_to_original[i] for i in SUGAR_IDS]
    pre = con["Presynaptic_Index"].astype(int)
    post = con["Postsynaptic_Index"].astype(int)
    weights = con["Excitatory x Connectivity"].astype(float)

    selected = set(seed_indices)
    frontier = set(seed_indices)
    # Include all real edges among selected neurons. Selection is a bounded,
    # deterministic downstream expansion by total absolute connection weight.
    while len(selected) < count and frontier:
        mask = pre.isin(frontier) & ~post.isin(selected) & weights.ne(0)
        candidates = pd.DataFrame({"post": post[mask], "strength": weights[mask].abs()})
        ranked = candidates.groupby("post")["strength"].sum().reset_index()
        ranked = ranked.sort_values(["strength", "post"], ascending=[False, True])
        frontier = set(int(x) for x in ranked["post"].head(count - len(selected)))
        selected.update(frontier)

    ordered = sorted(selected)
    local_index = {original: i for i, original in enumerate(ordered)}
    mask = pre.isin(selected) & post.isin(selected) & weights.ne(0)
    edges = [
        {"source": local_index[int(a)], "target": local_index[int(b)], "weightUnits": float(w)}
        for a, b, w in zip(pre[mask], post[mask], weights[mask])
    ]
    nodes = [{"id": ids[i], "originalIndex": i, "stimulated": i in seed_indices} for i in ordered]
    manifest = {
        "schemaVersion": 1, "dataset": "FlyWire", "datasetVersion": "630",
        "repository": REPOSITORY, "commit": COMMIT,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceNeuronCount": len(comp), "sourceConnectionRowCount": len(con),
        "neuronCount": len(nodes), "connectionCount": len(edges), "requestedNeuronCount": count,
        "selection": "21 sugar sensory neurons from upstream example.ipynb; deterministic downstream expansion ranked by absolute original connection weight; induced edges only",
        "model": "Brian2 leaky integrate-and-fire, original constants and signed effective weights; NumPy code generation; isolated subset reset before each trial",
        "limitations": "An isolated real-connectome subset, not a whole-brain reproduction. Boundary inputs are omitted. Flower/dialogue mappings are application design, not biological emotion or social cognition. No neural learning is implemented.",
        "sources": sources,
    }
    subset = {"manifest": manifest, "nodes": nodes, "edges": edges, "stimulusIds": SUGAR_IDS,
              "parameters": {"restMv": -52, "resetMv": -52, "thresholdMv": -45,
                             "membraneMs": 20, "synapseDecayMs": 5, "refractoryMs": 2.2,
                             "delayMs": 1.8, "weightScaleMv": 0.275,
                             "poissonScale": 250, "dtMs": 0.1}}
    target = output / "subset.json"
    target.write_text(json.dumps(subset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    manifest["subsetSha256"] = digest(target)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "neurons": len(nodes), "connections": len(edges),
                      "sha256": manifest["subsetSha256"]}), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "flywire")
    parser.add_argument("--neurons", type=int, default=384)
    args = parser.parse_args()
    if not 128 <= args.neurons <= 512:
        parser.error("--neurons must be between 128 and 512")
    prepare(args.output, args.neurons)
