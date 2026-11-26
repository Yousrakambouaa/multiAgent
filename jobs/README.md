# Local game jobs

The studio creates one dated directory per request, with a saved job record,
specification, generated Unity project, validation evidence, and delivery files.
`current.txt` identifies the current job; `studio_settings.json` stores the local
default budget.

Everything here except this README is excluded from Git. Jobs can contain private
prompts, Telegram identifiers, local interpreter paths, API cost records, and
user-owned art. Back up the complete job directory to retain resumability.
Deleting a generated Unity project's import cache is different from deleting its
saved job or assets; do not remove job data while the studio is using it.

The public source tree does not ship generated games or claim that a local game
is a reproducible result of the current model catalog. See the root README for
offline tests and explicit Unity verification commands.
