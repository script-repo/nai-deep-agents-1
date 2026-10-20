# AGENTS.md — Acme Orders service

Last updated: 2023-02-14

## Setup

- Always use npm to install dependencies (`npm install`).
- Requires Node 16 or newer.
- Start the dev server with `npm run dev`; the entry point is `src/server.ts`.
- Environment variables live in `.env.example`; copy it to `.env` before running.

## Code style

- Use TypeScript strict mode everywhere.
- Prefer named exports. Never use default exports.
- Keep functions under 40 lines; extract helpers into `src/lib/`.
- Run the linter before every commit: `npm run lint`.

## Architecture

The API layer lives in `src/api/`, the database access layer in `src/db.ts`.
Business rules for discounts are in `src/rules/discounts.ts`.
See `docs/architecture.md` for the full picture.

## Testing

- Write tests with Jest; put them next to the file as `*.test.ts`.
- Run the linter before every commit: `npm run lint`.
- TODO: document the integration test setup.
