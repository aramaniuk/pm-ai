"""Transcript source adapters behind `pm_ai.ports.TranscriptSourcePort` (AD-23).

Two from day one: Graph as primary (`graph_transcripts`, story 33e), and a
manual watched folder (`manual`) so the meeting pipeline stays developable and
testable without tenant-admin consent — which is outside this project's control.
Both satisfy the port since `11b`; reading a capture's text is
`pm_ai.core.transcripts`'.
"""
