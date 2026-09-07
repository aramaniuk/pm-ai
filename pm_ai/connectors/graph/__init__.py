"""Microsoft Graph — the delegated auth this connector family runs on, and the wire.

Three modules, in the order they were built and in the order they depend:

- `auth` (story 33a) — the device-code flow, the five refusals, and the silent
  refresh. Nothing here can ask Graph anything without it.
- `client` (story 33b) — one authenticated read-only `GET`, followed as far as
  the provider's own `@odata.nextLink` chain goes, under a page cap, a
  seen-link set, an origin check and a wall-clock budget.
- `calendar` (story 33b) — `calendarView` over a bounded window, returning rows
  whose instants are aware UTC and the coverage the fetch actually earned.

No `ConnectorPort` yet: `GraphConnector`, the `Meeting` records and
`CALENDAR_EVENT_HELD` are story 33c, which reads `calendar`'s rows. Chat and
channel messages are 33d and transcripts are 33e.
"""
