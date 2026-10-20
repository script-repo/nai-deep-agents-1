# Decisions

- 2023-03-02: Switched the package manager from npm to pnpm (lockfile is `pnpm-lock.yaml`).
- 2023-05-19: Chose zod for request validation (`src/api/orders.ts`).
- 2023-08-01: Moved the payments adapter out of `src/legacy/`; the old folder is gone.
- 2024-01-10: Upgraded to Node 20 (`.nvmrc`).
