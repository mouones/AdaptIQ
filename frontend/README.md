# AdaptIQ Frontend

React, TypeScript, Vite, Tailwind, and Playwright frontend for the AdaptIQ learning app.

## Runtime Shape

- App routes: `src/App.tsx`
- Auth state: `src/context/AuthContext.tsx`
- Shared API helper: `src/services/http.ts`
- Page components: `src/pages/`
- Admin dashboard: `src/pages/AdminDashboard.tsx`
- Service clients: `src/services/`

## Local Setup

```powershell
Set-Location frontend
npm install
npm run dev
```

Default local frontend URL: `http://localhost:5173`.

The backend API base comes from `VITE_API_URL`; when unset it falls back to the local backend.

## Auth And API Calls

- Browser auth uses backend-set cookies, not persistent bearer token storage.
- `authFetch` sends `credentials: "include"` and attaches `X-CSRF-Token` when the CSRF cookie is present.
- Do not write `adaptiq_token`, `adaptiq_user`, or chat history to `localStorage`.
- LLM calls are backend-only. Do not add browser-side LLM clients or Vite-injected LLM keys.

## Admin Dashboard

The admin dashboard includes:

- overview cards, daily analytics, and question source provenance groups
- user management and timed bans
- question and concept management
- custom-topic approval from backend candidates
- governance block rules and audit log
- DB inspector with backend redaction
- monitoring telemetry

The question source cards come from backend grouping of `QuestionBank.source`: generated, seeded, admin, unknown, category counts, and raw source counts.

Custom-topic approval uses:

- `GET /api/admin/custom-topics/candidates`
- `POST /api/admin/custom-topics/approve`
- `POST /api/admin/custom-topics/toggle-active`
- `GET /api/custom/topics` for user-facing approved topic visibility

## Validation

```powershell
npm run lint
npm run build
npm run test:e2e
```

The Playwright smoke tests check cookie auth/localStorage privacy and the admin custom-topic approval UI contract.

## Production Notes

- Build with `npm run build`.
- Serve `dist/` behind a static host/CDN.
- Do not cache authenticated API responses at CDN/proxy layers.
- Configure backend CORS for the real frontend origin.
- Keep frontend environment files local and untracked.
