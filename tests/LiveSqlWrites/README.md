# Live SQL write acceptance slice

This explicit diagnostic uses the actual native C# transport against an authorized Bunny Database. It creates one uniquely named probe table, commits 1,200 rows containing 4,915,200 payload bytes through HTTP pages, then verifies that a later-page constraint failure rolls back earlier pages. It verifies the exact probe schema before dropping only that table and confirms its absence. It does not write canonical catalog tables, start Magic Containers or create a database.

Run only against a database you are authorized to test. Verify the account/resource identity first, save a private creation-intent journal with the generated table name, and retain that journal if a network failure interrupts cleanup. This diagnostic is not run by normal unit CI.

Provide `JACRED_DATABASE_URL`, a scoped `JACRED_DATABASE_WRITE_TOKEN`, and `JACRED_TEST_TABLE` through the private process environment. The table name must match `jacred_acceptance_` followed by 32 lowercase hexadecimal characters; generate a fresh random value and save it in the journal before execution. Do not put real tokens in command arguments or source files. The account root API key is neither required nor accepted by this harness.

```sh
dotnet run --project tests/LiveSqlWrites/LiveSqlWrites.csproj
```

A successful final JSON report includes the compiled full source SHA, row/payload counts, late-page rollback and confirmed cleanup. A failed run must be reconciled from its saved table identity before retrying; do not blindly recreate or adopt existing tables. The original execution also independently compared all seven canonical tables before/after and confirmed the real 22-record catalog remained unchanged. This scoped probe does not establish full migration, deployed worker, durability, cost or production acceptance.
