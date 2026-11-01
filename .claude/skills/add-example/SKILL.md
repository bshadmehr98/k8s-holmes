---
name: add-example
description: Add a runnable, explained example to examples/ that the tests execute. Use when a change adds a concept users should see, or the user asks for an example.
---

# Add an example

k8sholmes has no examples yet. The first one also creates the folder and its test.

1. One concept per file: `examples/<name>.py`:
   - a module docstring with **What** (what it shows), **How** (the calls it uses) and **Why** (the
     problem it solves);
   - offline and fast: a recorded cluster from `k8sholmes.testing` (no kubeconfig, no network), the fake
     reasoner, a temporary folder for the case file;
   - prints something that shows the result and asserts the key facts.
2. List it in examples/README.md (create it with the first example), in reading order.
3. A test in `tests/e2e/` runs every file in examples/ (create it with the first example); run it.
4. An examples/quickstart.py is also run by `scripts/check.sh` against the built wheel alone.
5. If `design/current.md` lists the examples, add it there too.
