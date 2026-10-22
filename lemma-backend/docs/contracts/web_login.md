# web_login contract

What every `web_login` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `web_login.delete` | DELETE | `/web-logins` | Sign your browser out of a site |
| `web_login.list` | GET | `/web-logins` | List the sites your browser is signed in to |
| `web_login.sign_in.answer` | POST | `/web-logins/sign-ins/{conversation_id}/{tool_call_id}/answer` | Say whether you signed in |
| `web_login.sign_in.pending` | GET | `/web-logins/sign-ins/{conversation_id}/{tool_call_id}` | What a sign-in link is asking for |

<!-- /generated:operations -->

## `web_login.list`

Every site the caller's sandbox browser is signed in to, grouped by
registrable domain so `example.com` and `api.example.com` read as one login
rather than two.

Read from the browser each time, not from a table — the browser keeps its own
profile, so what it holds is the only true answer. **Never carries a secret**,
and that is now structural in a stronger sense than a response shape with no
field for one: cookie values never cross the sandbox boundary at all. What
comes back is a host, a count and an expiry.

Which browser: every workspace -- one per person, pod and actor -- has its own,
with its own sign-ins. `pod_id` names the pod and `agent` one of its named
agents; without `agent` it is the pod assistant's, and without `pod_id` the
caller's personal workspace. Both are checked the way the `workspace` routes
check them, and `web_login.delete` takes the same pair. A sign-in answered
through `web_login.sign_in.answer` goes into the browser of the conversation's
agent.

Does not start a paused computer unless `wake=true`, and answers `sleeping`
instead. Rendering a settings page should not be what spins one up, and unlike
the table read this replaces, answering costs a round trip into the sandbox.

## `web_login.delete`

Signs the browser out of a site. Addressed by origin rather than id, because
that is what the person recognises and what the agent asked about.

This really signs it out — it drops the site's cookies and its stored data.
Its predecessor deleted Lemma's encrypted copy and left the browser exactly as
it was, which is why every caller had to carry a disclaimer saying so. Nothing
the person is signed in to in their *own* browser is touched.

The stored-data half was broken on arrival and is fixed here: only bare hosts
were sent to `Storage.clearDataForOrigin`, and local storage is keyed by full
origin, so a site on a non-default port kept its storage. The origins of any
open page now go too. The residual gap, stated because it is real: a site on a
non-default port with no page open still has only its cookies cleared, because
nothing then names the port.

Refuses with 409 when the computer is not running, rather than reporting a
success it did not achieve, and 422 when the origin is not one a session could
belong to. Answers `forgotten: false` when the browser was holding nothing for
that site, which is not an error.
