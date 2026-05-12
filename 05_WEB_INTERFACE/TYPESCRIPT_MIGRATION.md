# TypeScript Migration (Safe / Non-Breaking)

This folder now includes TypeScript tooling without changing current runtime behavior.

## Goal
- Keep the current UI/UX exactly as-is.
- Migrate progressively from inline JavaScript to TypeScript modules.
- Preserve all existing API endpoints and Python server behavior.

## Added
- `package.json` (TypeScript scripts)
- `tsconfig.json`
- `src/types/workitem-scope.ts` (typed contracts)
- `src/client/workitem-scope-api.ts` (typed API helpers)

## Current behavior
- Existing pages still run with current inline scripts (no regression expected).
- TypeScript files are ready for incremental adoption.

## Commands
From `05_WEB_INTERFACE`:

```powershell
npm install
npm run build
```

Generated JS output goes to:
- `05_WEB_INTERFACE/static/ts`

## Next step (recommended)
1. Start with `workitem_scope.html`.
2. Move one feature block at a time (filters, table rendering, scope rendering) from inline script to TS.
3. After each block, validate page behavior before moving next block.

This keeps delivery stable and enterprise-safe.

