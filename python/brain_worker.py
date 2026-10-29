"""JSONL worker: real FlyWire-derived isolated circuit, no robot control."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import brian2 as b2
import numpy as np

b2.prefs.codegen.target = "numpy"
PROTOCOL = sys.stdout
MN9_ID = "720575940660219265"


def emit(value: dict) -> None:
    PROTOCOL.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    PROTOCOL.flush()


def load_data(path: Path) -> tuple[dict, str]:
    if path.is_dir():
        path = path / "subset.json"
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("manifest", {}).get("dataset") != "FlyWire":
        raise ValueError("Expected a prepared real FlyWire subset, not synthetic data.")
    if not data.get("edges") or not data.get("stimulusIds"):
        raise ValueError("FlyWire circuit has no connections or stimulus IDs.")
    if any(not isinstance(n["id"], str) for n in data["nodes"]):
        raise ValueError("All FlyWire IDs must be JSON strings.")
    actual_hash = hashlib.sha256(raw).hexdigest()
    manifest_path = path.with_name("manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("subsetSha256") != actual_hash:
            raise ValueError("Subset SHA-256 differs from its provenance manifest.")
    return data, actual_hash


def number(payload: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return float(value)


def simulate(data: dict, data_hash: str, payload: dict) -> dict:
    started = time.perf_counter()
    stimulus_hz = number(payload, "stimulusHz", 100, 0, 250)
    duration_ms = number(payload, "durationMs", 200, 50, 500)
    seed_value = number(payload, "seed", 42, 0, 2147483647)
    if not seed_value.is_integer():
        raise ValueError("seed must be an integer")
    seed = int(seed_value)
    nodes, edges, params = data["nodes"], data["edges"], data["parameters"]
    index = {node["id"]: i for i, node in enumerate(nodes)}
    silence_ids = payload.get("silenceIds", [])
    if not isinstance(silence_ids, list) or any(not isinstance(i, str) or i not in index for i in silence_ids):
        raise ValueError("silenceIds must contain known FlyWire ID strings")

    b2.start_scope()
    b2.seed(seed)
    b2.defaultclock.dt = params["dtMs"] * b2.ms
    namespace = {
        "v_rest": params["restMv"] * b2.mV,
        "v_reset": params["resetMv"] * b2.mV,
        "v_threshold": params["thresholdMv"] * b2.mV,
        "t_membrane": params["membraneMs"] * b2.ms,
        "t_decay": params["synapseDecayMs"] * b2.ms,
    }
    neurons = b2.NeuronGroup(
        len(nodes),
        "dv/dt = (v_rest - v + g) / t_membrane : volt (unless refractory)\n"
        "dg/dt = -g / t_decay : volt (unless refractory)\nrfc : second",
        method="linear", threshold="v > v_threshold", reset="v = v_reset; g = 0*mV",
        refractory="rfc", namespace=namespace,
    )
    neurons.v = namespace["v_rest"]
    neurons.g = 0 * b2.mV
    neurons.rfc = params["refractoryMs"] * b2.ms
    synapses = b2.Synapses(neurons, neurons, "w : volt", on_pre="g += w",
                           delay=params["delayMs"] * b2.ms)
    sources = np.array([edge["source"] for edge in edges], dtype=int)
    targets = np.array([edge["target"] for edge in edges], dtype=int)
    synapses.connect(i=sources, j=targets)
    weights = np.array([edge["weightUnits"] for edge in edges], dtype=float)
    if silence_ids:
        weights[np.isin(sources, [index[i] for i in silence_ids])] = 0
    synapses.w = weights * params["weightScaleMv"] * b2.mV
    inputs = []
    for fly_id in data["stimulusIds"]:
        i = index[fly_id]
        neurons[i].rfc = 0 * b2.ms
        inputs.append(b2.PoissonInput(neurons[i], "v", N=1, rate=stimulus_hz * b2.Hz,
                                     weight=params["weightScaleMv"] * params["poissonScale"] * b2.mV))
    monitor = b2.SpikeMonitor(neurons)
    network = b2.Network(neurons, synapses, monitor, *inputs)
    network.run(duration_ms * b2.ms)
    counts = np.asarray(monitor.count, dtype=int)
    rates = counts / (duration_ms / 1000)
    spike_times = np.asarray(monitor.t / b2.ms, dtype=float)
    spike_indices = np.asarray(monitor.i, dtype=int)
    bins = np.linspace(0, duration_ms, 21)
    histogram, _ = np.histogram(spike_times, bins=bins)
    selected = sorted(range(len(nodes)), key=lambda i: (-counts[i], nodes[i]["id"]))[:64]
    node_rates = [{"id": nodes[i]["id"], "rateHz": round(float(rates[i]), 4),
                   "spikeCount": int(counts[i]), "stimulated": nodes[i]["stimulated"]} for i in selected]
    downstream = [i for i, node in enumerate(nodes) if not node["stimulated"]]
    graph_indices = set(selected)
    graph_edges = [
        {"source": nodes[e["source"]]["id"], "target": nodes[e["target"]]["id"],
         "weightMv": round(float(w) * params["weightScaleMv"], 5)}
        for e, w in zip(edges, weights)
        if e["source"] in graph_indices and e["target"] in graph_indices
    ][:256]
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return {
        "mode": "flywire", "computeMode": "live", "scope": "induced-subset", "engine": "Brian2",
        "datasetVersion": data["manifest"]["datasetVersion"], "dataHash": data_hash,
        "sourceCommit": data["manifest"]["commit"],
        "neuronCount": len(nodes), "connectionCount": len(edges),
        "stimulusNeuronCount": len(data["stimulusIds"]), "stimulusHz": stimulus_hz,
        "durationMs": duration_ms, "simulationTimeMs": duration_ms, "wallTimeMs": elapsed,
        "seed": seed, "silenceIds": silence_ids, "spikeCount": int(counts.sum()),
        "activeNeurons": int(np.count_nonzero(counts)),
        "meanRateHz": round(float(rates.mean()), 4),
        "downstreamRateHz": round(float(rates[downstream].mean()), 4) if downstream else 0,
        "mn9RateHz": round(float(rates[index[MN9_ID]]), 4) if MN9_ID in index else None,
        "nodes": node_rates, "nodeRates": node_rates, "edges": graph_edges,
        "traces": [{"timeMs": round(float(bins[i]), 2), "spikes": int(value)}
                   for i, value in enumerate(histogram)],
        "spikes": [{"id": nodes[int(i)]["id"], "timeMs": round(float(t), 3)}
                   for i, t in zip(spike_indices[:1000], spike_times[:1000])],
        "displayLimits": {"nodes": 64, "edges": 256, "spikes": 1000},
        "interpretation": "Real isolated FlyWire subset activity. Dialogue-to-stimulus and activity-to-action mappings are application-defined; no biological emotion, neural learning, or autonomous physical execution is claimed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    try:
        data, data_hash = load_data(args.data)
        emit({"type": "ready", "protocolVersion": 1, "mode": "flywire", "scope": "induced-subset",
              "datasetVersion": data["manifest"]["datasetVersion"], "dataHash": data_hash,
              "neuronCount": len(data["nodes"]), "connectionCount": len(data["edges"])})
    except Exception as exc:
        emit({"type": "fatal", "error": str(exc)})
        raise SystemExit(1)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get("op") != "simulate" or not isinstance(request.get("requestId"), str):
                raise ValueError("Expected simulate request and string requestId")
            emit({"type": "started", "requestId": request["requestId"]})
            with contextlib.redirect_stdout(sys.stderr):
                result = simulate(data, data_hash, request.get("payload", {}))
            emit({"type": "result", "requestId": request["requestId"], "result": result})
        except Exception as exc:
            print(f"Brain request failed: {exc}", file=sys.stderr, flush=True)
            emit({"type": "error", "requestId": request.get("requestId"), "error": str(exc)})


if __name__ == "__main__":
    main()
