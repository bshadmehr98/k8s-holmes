# Manifests for the cluster tests

One folder per failure pattern (`crashloop-oom/`, `image-not-found/`, `service-no-endpoints/`, …), each a
small set of resources that reproduces the pattern in a throwaway local cluster. Written when design 0001 is
accepted and its checks are built; see `.claude/skills/real-cluster-tests/SKILL.md`.
