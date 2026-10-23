# Recipe — call a connector operation from an app

Run a third-party action (send email, post message, …) on the user's connected
account, from the app. (← `apps.md`)

## The model (see `connectors.md`)

A **connector** (catalog: `gmail`, `slack`…) has an org **auth-config**; each user
has an **account** under it; the connector exposes **operations**. The app executes
an operation **on the user's account** (delegated) — it never holds credentials.
You address operations by the **auth-config name**, which you find with
`lemma connectors overview`.

## Grants it needs

The app runs a connector only with `connector.use` on it among its own grants:
`lemma apps permissions add <app> connector:<slug>:use`. Without it a call
is `403 APP_NOT_GRANTED` naming the connector, even for
a viewer who could run it themselves. The grant decides *whether* the app may
use the connector; the connector still decides *whose account* it runs as.

## Discover, then execute

Every `connectors.operations.*` call takes a **scope** first — `{ organizationId,
authConfigName }` — because auth configs are org-scoped, not pod-scoped. The pod knows
its org, so resolve it once:

```ts
// A pod connector is addressed by its slug; the pod declares it (`lemma
// connectors pod-list`). It decides whose account runs the call: the signed-in
// person's own (PER_MEMBER) or the one the pod shares (SHARED).
const slug = "gmail";

// 1. operations this connector offers (kind-specific):
const ops = await client.connectors.pod.operations(slug);           // → array
//   ...or narrow by intent: pod.operations(slug, { query: "send email", limit: 5 })

// 2. the operation's input schema (drive your form / validate the payload):
const detail = await client.connectors.pod.operation(slug, "gmail_send_email");

// 3. execute — no account argument; the pod connector picks it:
const result = await client.connectors.pod.execute(
  slug,
  "gmail_send_email",
  { recipient_email: "bob@example.com", subject: "Update", body: "…" },
);
```

`pod.operations` returns the items array directly. Org-level discovery
(`connectors.operations.list(scope)`) still browses an installation before a pod
declares it, but cannot run anything.

## Make it a safe action

- **Discover, don't guess** operation names — list/search first; names are
  kind-specific (`package` and `composio` differ).
- **Confirm side-effects.** An operation that sends/posts/charges is irreversible —
  gate it behind an explicit confirm and show what will happen.
- **Handle the not-connected case.** When the person has no account for a
  PER_MEMBER connector, or the shared one is gone or expired, the call throws a
  `ConnectionRequiredError` (`CONNECTION_REQUIRED`, `REAUTH_REQUIRED`,
  `CONNECTOR_NEEDS_ACCOUNT`, `CONNECTOR_UNAVAILABLE`,
  `CONNECTION_REQUIRES_PERSON`). The app can't connect on their behalf — show
  the state and send them to `connectUrl`, the pod's Connections page, which
  returns them once connected (see below).

```ts
import { ConnectionRequiredError } from "lemma-sdk";   // no-build: window.LemmaClient.ConnectionRequiredError

try {
  await client.connectors.pod.execute(slug, "gmail_send_email", payload);
} catch (error) {
  if (!(error instanceof ConnectionRequiredError)) throw error;
  // error.code says which: REAUTH_REQUIRED is "sign in again" (error.isReauth),
  // CONNECTOR_NEEDS_ACCOUNT is "someone has to share an account".
  showNotConnected({
    message: error.message,                        // the server's own sentence
    action: error.connectUrl
      ? (error.isReauth ? "Reconnect" : "Connect your account")
      : null,                                      // nothing this person can do: say who can
    onAction: () => error.connect(),               // opens connectUrl (a new tab from inside the app frame)
  });
}
```

  `connectUrl` is `null` when the person can't fix it themselves (a shared
  account only its owner can reconnect, an installation an admin turned off) —
  say so instead of offering a button that cannot work. Draw this state up
  front, not only after a failed send.
- For server-side calls (a function/agent acting for the user), the same operation
  runs through the in-function SDK with a granted connector — see `functions.md`.

> Exact surface: `cat node_modules/lemma-sdk/dist/namespaces/connectors.d.ts`; the org
> auth-config names come from `lemma connectors overview`.
