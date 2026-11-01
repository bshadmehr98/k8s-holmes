# Examples

Runnable, offline and fast: each opens with a What / How / Why docstring, prints what it shows and asserts
the key facts. `tests/e2e/test_examples.py` runs every file here; `scripts/check.sh` runs
`quickstart.py` against the built wheel alone.

1. [`quickstart.py`](quickstart.py): the demo environment and its offline investigator, an investigation,
   the report.
2. [`fake_cluster_and_scripted_model.py`](fake_cluster_and_scripted_model.py): resources in memory and a
   model you script; the gate refuses a probe, an invented citation becomes a limitation.
3. [`command_capability.py`](command_capability.py): a capability declared in TOML, loaded with an
   allow-list, run through the gate.
4. [`reports_and_follow_through.py`](reports_and_follow_through.py): a report with a human edit, a
   proposal verified against the "after the fix" recording, two saved runs compared by key.
