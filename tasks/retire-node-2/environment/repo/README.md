# pulse

Bookings for Pulse gyms: the member app and the studio kiosks book classes through this API.

- `pulse/`: the API (python, stdlib only; runs on the platform's `python-web` base image)
- `ledger/`: ledgerd, the bookings ledger (append-only, one leader). Ops notes in `ledger/OPERATIONS.md`.
- `deploy/k8s/`: manifests for the `bookings` namespace on the prod cluster
- `docs/runbooks/`: on-call runbooks

## Build and ship

Images are built with SimCloud (`sc build`); see the platform skill. We tag with the version in
`pulse/__init__.py`. Deploys: `kubectl apply -f deploy/k8s/` (we keep meaning to set up GitOps).

```
sc build api --source . --tag 2026.09.3 --cmd 'python -m pulse' --port 8080
sc build ledger --source ledger --workdir /opt/ledger --tag 1.4.2 --cmd 'python /opt/ledger/ledgerd.py'
```

## Local

`LEDGER_DATA=/tmp/l python ledger/ledgerd.py &` then `LEDGER_URL=http://127.0.0.1:7000 python -m pulse`.
