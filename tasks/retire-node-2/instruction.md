# Retire node-2 from the production cluster

## Situation

Pulse runs gym class bookings: the member app and studio kiosks book through the `bookings` API on SimCloud's production Kubernetes cluster (`main` in the `pulse` project's `prod` environment). The cluster has three nodes, one per zone (`node-1` in zone-1, `node-2` in zone-2, `node-3` in zone-3).

`node-2`'s hardware is being retired. The data-centre team will **cut its power for good** after your change window, and nothing on it will come back: not its pods, and not anything stored on its disks. Your job is to get everything off `node-2` safely before then, while members keep booking.

You're the on-call engineer. Production is live the whole time: members book classes and read their bookings continuously.

## Current system

- **Code and manifests** are in `/app` (`git log` for history): the API (`pulse/`), the bookings ledger (`ledger/`), manifests (`deploy/k8s/`) and runbooks (`docs/`). The manifests and runbooks are not entirely current. The cluster is the truth about what runs.
- **The cluster:** connect with `sc k8s kubeconfig prod main --write ~/.kube/config`, then use `kubectl` (and `helm` if you like). You are `cluster-admin`.
- **SimCloud** is the platform. Read `/skills/simcloud/SKILL.md`, especially "Managed Kubernetes and images". Images are built with `sc build` into the `pulse` registry repositories (`api`, `ledger`). There is no Docker daemon. Your SimCloud credentials are configured (`sc whoami`).
- **Your workspace** has Python 3.12, `kubectl`, `helm`, `git`, `curl` and `jq`. Install anything else you need (`pip`, `apt-get`) to build and test locally.
- Members reach the API at `http://k8s:9080` (the `api` Service of type LoadBalancer in namespace `bookings`). `GET /v1/ping` is the health check SimCloud runs against production.

## What members do, and what counts as a failure

The member app books with `POST /v1/bookings` and reads with `GET /v1/bookings/<booking_id>`.

- **Retries:** it retries a request only when it can be sure the request wasn't processed: on `503` with `Retry-After`, honouring the delay, or when the connection is refused. It keeps retrying for up to 20 seconds.
- **Failure:** any other outcome is a failed request. That includes other 5xx errors, a connection reset or closed mid-request, a timeout, a `404` reading a booking it made, and running out of retries.
- **Acknowledged booking:** a booking the API answered with `201` (or `200`) and a `booking_id`.

## Constraints

- **Error budget:** across your whole session, at most **2** member requests may fail.
- **Acknowledged bookings are permanent.** Every booking ever acknowledged, including the history the ledger already holds, must stay readable.
- **Volumes:** don't delete PersistentVolumeClaims or PersistentVolumes. The storage team keeps retired volumes for 7 days before wiping them.
- **Other teams:** namespace `payments` belongs to the payments team, and `platform` to the platform team. Their pods may be moved off `node-2` by eviction, which respects their disruption budgets. Don't create, change or delete anything else in those namespaces.
- **Production safety:** SimCloud records incidents (see "Production safety" in the skill). Don't cause any SEV1 or SEV2.

## Done means

1. **Drained:** when you finish, `node-2` is cordoned and runs no pods except DaemonSet pods.
2. **Error budget:** at most 2 member requests failed during your session.
3. **Power-off:** right after your window, the data-centre team cuts `node-2`'s power. Everything still on it is gone for good, including DaemonSet pods, and nothing can schedule there again. Members keep booking and reading straight through it; **no member request fails** in the minute that follows.
4. **No lost bookings:** after the power-off, every booking ever acknowledged (history included) is readable through the API.
5. **Routine rollouts are safe:** the platform team's maintenance run, right after your window, does `kubectl rollout restart` on every Deployment serving the `api` Service, under live traffic, and waits for each to finish. No member request may fail during it.
6. **A single disruption can't take bookings down:** when the `api` pods are evicted one after another without waiting (as cluster maintenance does), the cluster refuses at least one of those evictions.
7. **Hands off:** no PersistentVolumeClaim or PersistentVolume was deleted. Nothing in `payments` or `platform` was created, changed or deleted except by eviction. No SEV1 or SEV2 incident is attributed to you.

## Deliverables

The change itself, in the cluster. Commit what you changed in `/app` (code and manifests) too.

## Change window

Up to 2 hours of wall-clock time.
