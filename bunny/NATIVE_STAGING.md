# Native staging control plane

This is an advanced infrastructure preview inside the existing JacRed Bunny CLI. It is not a second installer and does not complete the v0.5.4 gate. The default `setup` command remains the original FileDB deployment path.

## Plan

```sh
python3 bunny/cli.py native-staging plan
```

The plan creates nothing. The displayed $1.22 initial estimate combines the published Storage monthly minimum and first Edge billing increment. Database public preview is free as checked on 3 November 2026. It is not an account bill prediction or hard cost ceiling; traffic is metered separately.

## Provision isolated infrastructure

Use a published immutable source-SHA tag of this fork and a numeric public GHCR registry ID verified in your own Bunny account. The API checks the image digest before creating resources. Do not copy another account's registry configuration blindly.

```sh
python3 bunny/cli.py native-staging provision \
  --source-sha "$REVIEWED_SOURCE_SHA" \
  --image "ghcr.io/vbrekher/jacred-bunny:sha-$REVIEWED_SOURCE_SHA" \
  --registry-id "$PUBLIC_GHCR_REGISTRY_ID" \
  --max-estimated-usd 2 --apply
python3 bunny/cli.py native-staging status
```

Authentication comes from `BUNNYNET_API_KEY` or the authorized Bunny CLI profile. An interactive terminal can prompt for a missing key. Do not pass secrets in command arguments.

The command creates one AMS-primary Database with EU storage, one DE Storage zone and one standalone Edge Script with its linked HTTPS Pull Zone. A random deployment name fits the **actual live Database limit of 16 characters**. The OpenAPI description currently advertises a different limit; live validation takes precedence.

The local deployment manifest and credentials are separate mode-600 files inside a mode-700 directory. The manifest includes owner, creator, exact source SHA, image digest, timestamps, resource identities, migration state and `production_ready: false`. The account API key is not copied into that manifest.

A repeated provision verifies saved identities instead of creating duplicates. Simultaneous mutation commands are rejected by an OS file lock. A failed or ambiguous POST leaves its intent recorded and stops; it cannot be retried blindly. Reconcile the account response before resuming. Never adopt another product's resource because it has a similar name.

## Current boundaries

This command provisions the three data-plane resources only. It does not yet install the SQL collector, apply migrations, configure a 24-hour scheduler, restore production backups, or publish a ready catalog. Those are required before the normal installer can offer native deployment as its default. `Deployment.wire_edge` is an internal checksum-checked publishing primitive; its credentials stay server side and its database token is read-only.

The manifest's expiry records intent; **this data-plane primitive does not enforce automatic teardown**. The separate operator staging run documented in the evidence used a compute watchdog. Empty data-plane resources remain in the account until deliberately cleaned up. Storage allocation and Edge request increments can remain billable. No destructive cleanup or production cutover is implemented here.

See the [acceptance matrix](../docs/ACCEPTANCE_MATRIX.md) for remaining gates.
