# Packaging notes (cyberphy)

## Retired: Cloudera Manager

The following modules are **no longer built** and are not part of cyberphy delivery:

| Module | Former role |
|--------|-------------|
| `cyber-parcel/` | Cloudera Manager parcel |
| `cyber-csd/` | Cloudera Service Descriptor |

They may still exist in git history or as unused directories; do not add them back to the Maven reactor.

## Current delivery

| Channel | Location |
|---------|----------|
| Air-gap K8s interactive stack | Repo root [`zarf/`](../zarf/) |
| AWS / RKE2 provision + deploy | Repo root [`infra/`](../infra/) |
| Flink pipeline JARs / jobs | This tree (`mvn clean install -DskipTests`) |

Runtime Java modules such as `cyber-services` (worker service, etc.) remain in the reactor when needed for non-CM deployments.
