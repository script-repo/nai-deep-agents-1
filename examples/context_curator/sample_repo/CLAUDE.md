# CLAUDE.md

Project notes for Claude Code.

## Commands

- Use pnpm for all package management; never run npm install (it breaks the lockfile).
- Dev server: `pnpm dev`. Tests: `pnpm test`.
- This project targets Node 20.

## Code style

- Use TypeScript strict mode everywhere.
- Prefer named exports. Never use default exports.
- Keep functions under 40 lines; extract helpers into `src/lib/`.

## Architecture

The API layer lives in `src/api/`, the database access layer in `src/db.ts`.
Business rules for discounts are in `src/rules/discounts.ts`.
See `docs/architecture.md` for the full picture.

## Gotchas

- The orders endpoint in `src/api/orders.ts` validates input with zod.
- The old payment adapter in `src/legacy/payments.js` is still wired in; do not touch it.
- FIXME: remove the feature flag notes once the migration ships.
