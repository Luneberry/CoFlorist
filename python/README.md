# FlyWire worker

CoFlorist runs a **real but isolated FlyWire v630 connectome subset**, not a full fruit-fly brain or a biological model of emotion. The 21 sugar sensory neuron IDs are taken verbatim from the upstream example. `prepare_data.py` expands downstream by total absolute original connection weight to 384 neurons, then retains every original nonzero edge between those nodes. Edges preserve their signed `Excitatory x Connectivity` value, multiplied by the original 0.275 mV scale. Removing the rest of the brain changes circuit dynamics.

The upstream source is MIT licensed:
https://github.com/philshiu/Drosophila_brain_model/tree/91bdd1e7dcf193f3e7ca5a8933497fcef63b7960

The source files, upstream LICENSE, SHA-256 hashes and full string neuron IDs are retained in `data/flywire/`. `manifest.json` records the exact commit, file URLs, sizes, hashes and extraction rule. `subset.json` is verified against its manifest when the worker starts.

Brian2 2.9 creates a Windows CPU cache in the user's home during import, before preferences can be set. `configure_brian.py` applies a narrow, idempotent compatibility patch only in this project's `.venv`, allowing `COFLORIST_BRIAN_CACHE` to put that cache in `.work-tmp/brian-cpu`. It refuses to modify a system or bundled Python environment.

## Setup

On Windows, run `powershell -File scripts/setup-brain.ps1`, passing `-PythonExecutable` if the default bundled Python path is not available. The script creates only the workspace `.venv`, installs `python/requirements.txt`, and downloads pinned upstream files. The prepared source data is about 90 MB. The normal application does not download anything or require Python network access.

Default worker paths are `<project>/.venv/Scripts/python.exe` and `<project>/data/flywire/subset.json`. The settings UI may point at another prepared data file or its containing directory. Run `node python/test-brain-worker.mjs` for a real numerical smoke test.

## Runtime contract

Express uses `BrainWorker` from `server/brain-worker.js`; `start()`, `run(payload)`, `stop()` and `snapshot()` never use a shell. `run()` accepts `stimulusHz` (0–250, default 100), `durationMs` (50–500, default 200), an integer `seed` and optional `silenceIds` containing known string IDs. Outgoing connections of silenced neurons are zeroed for that trial only. Each trial starts from resting state. `stdout` is JSONL only, protocol/library logs go to `stderr`. Queue length is bounded to eight waiting jobs; startup and queued/computing jobs have timeouts. Stopping a worker rejects all outstanding jobs.

Python uses Brian2 with NumPy code generation to avoid a compiler installation. The model preserves the upstream membrane, synapse, delay and threshold constants and its Poisson stimulus method. Its reset statement includes only existing neuron state (`v`, `g`); upstream also contains an unused/unbound `w = 0` reset assignment. Python 3.12/Brian2 2.9 compatibility is validated locally; this is an adaptation, not a bit-for-bit reproduction of the older upstream environment.

`nodes`, `edges`, `spikes` and histogram `traces` contain measured values. Display output is bounded (64 most active neurons, up to 256 edges among those neurons, first 1000 spikes). Full-circuit neuron/edge counts and aggregate firing rates are calculated over all 384 nodes. Original FlyWire IDs are always JSON strings because they exceed JavaScript's safe-integer range. A simulation's `durationMs` is simulated biological time; `wallTimeMs` is measured computation time.

The application may map dialogue intent to stimulus rate and use the measured activity as an input to an explicit application policy. Those mappings are authored software choices, not evidence that the fly circuit understands flowers, speech, human readiness or emotion. Preference/failure memory belongs to the application database; this worker performs no neural learning. Pausing and manual robot control must never wait for brain computation.
