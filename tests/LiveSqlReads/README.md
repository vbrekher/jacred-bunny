# Live SQL native read slice

This explicit diagnostic uses a scoped read-only Bunny Database JWT and the actual native C# store. It compares every bucket returned by the native paged index against canonical SQL row hashes, and requires identical service-computed snapshots before and after. It writes no SQL data and starts no containers. It is not part of normal CI execution.

Provide `JACRED_DATABASE_URL` and `JACRED_DATABASE_READ_TOKEN` through the private process environment. Only scoped JWT-shaped credentials are accepted; never provide an account API key or put secrets in command arguments. Use an authorized test database. The diagnostic has a three-minute total deadline; the canonical SQL comparison query remains bounded, so this tool is a small live acceptance slice rather than an unrestricted export utility.

```sh
dotnet run --project tests/LiveSqlReads/LiveSqlReads.csproj
```

A successful report contains the compiled source SHA, bucket/record counts, matching native row hashes and an unchanged relational snapshot checksum. No row content or credentials are printed. A concurrent catalog change fails the comparison instead of accepting a mixed result. Synthetic large-response tests live separately in the normal unit suite; this command does not substitute small live data for full large-catalog or production acceptance.
