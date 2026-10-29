"""Reproducible 200 ms input sweep for the local FlyWire subset."""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from brain_worker import load_data, simulate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data/flywire/subset.json")
    args = parser.parse_args()
    data, data_hash = load_data(args.data)
    print("input_hz\tactive_neurons_mean\tdownstream_rate_hz_mean\tdownstream_rate_hz_range\tmn9_rate_hz_mean")
    for hz in [*range(0, 241, 20), 250]:
        runs = [simulate(data, data_hash, {"stimulusHz": hz, "durationMs": 200, "seed": seed}) for seed in (1, 2, 3)]
        downstream = [run["downstreamRateHz"] for run in runs]
        mn9 = [run["mn9RateHz"] for run in runs if run["mn9RateHz"] is not None]
        print(f"{hz}\t{statistics.mean(run['activeNeurons'] for run in runs):.1f}\t"
              f"{statistics.mean(downstream):.3f}\t{min(downstream):.3f}–{max(downstream):.3f}\t"
              f"{statistics.mean(mn9):.3f}" if mn9 else f"{hz}\t—\t—\t—\t—", flush=True)


if __name__ == "__main__":
    main()
