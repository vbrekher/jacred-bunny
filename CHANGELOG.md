# Changelog

## Unreleased — native SQL development

The v1.0.0 production gates remain incomplete. These changes extend the original JacRed .NET service and preserve its API, tracker roster and license; they do not announce a production release.

- Replace permissive certificate callbacks with platform validation in .NET tracker transports and operational Python scripts. The exact TLS image collected two additional SubsPlease records into Bunny SQL, increasing the real staging catalog from 20 to 22. [TLS changes](https://github.com/vbrekher/jacred-bunny/pull/26).
- Pin public announce probes to validated DNS results, disable redirects/proxy/cookies, exclude private passkey URLs and require a matching BEP 15 UDP response. Bound attempts and execution time. These probe changes have source/local transport validation; deployed all-tracker acceptance remains open. [Probe changes](https://github.com/vbrekher/jacred-bunny/pull/27).
- Page administrative SQL snapshot proofs over one read transaction, retaining the existing framed checksum and bucket receipt formats. Reject lost streams, unsafe routing and unconfirmed cleanup. Limit concurrent proofs and retain explicit row, bucket metadata and time limits. Local data exceeding the former 8 MiB response cap matches an independent verifier; full deployed large-catalog acceptance remains open.
- Commit large catalog writes over bounded HTTP pages inside one transaction, retaining optimistic guards and durable receipts. Remove the former 900-record/1,000-statement/4 MiB overall-write limits; retain per-statement transport bounds. Return the stored revision when the same delivery is retried from a newer snapshot. Local tests cover later-page rollback, stale grouped moves and lost commit responses.
- Enforce journalled no-store rules on owned staging CDN zones and validate actual authenticated HTTP headers. Preserve digest-pinned payload history across update/rollback and keep staging compute bounded. [CLI policy](https://github.com/vbrekher/jacred-bunny/pull/25).

See the [acceptance matrix](docs/ACCEPTANCE_MATRIX.md) for mandatory remaining work and scoped evidence. Existing historical implementation documents remain in `docs/`.
