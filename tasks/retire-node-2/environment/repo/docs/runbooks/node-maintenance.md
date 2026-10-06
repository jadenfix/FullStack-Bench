# Node maintenance (written for the old VM fleet, 2025)

1. `kubectl cordon <node>`
2. `kubectl drain <node> --ignore-daemonsets`
3. tell #platform the node is empty

Our services are stateless, so this is safe any time. If drain hangs on a PDB, wait; if it hangs
more than 10 minutes ask the owning team.
