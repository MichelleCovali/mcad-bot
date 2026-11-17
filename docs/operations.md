# MCAD operations workspace

The public site and the `/admin` workspace use the same API and published
incident data. The workspace is private. The explorer supports combined text,
incident type, country and year filters across map and list modes, including
records with no coordinates in the list.

## First sign-in

1. Deploy a release with schema migrations 5–8. The production image applies
   migrations on startup; take a database backup before the first deployment.
2. In an interactive shell on the deployed Railway `mcad-web` service, run
   `python -m mcad create-admin --email name@example.com`. Enter a unique
   password at the masked prompt. The same command works locally after
   `python -m mcad migrate`. To recover an account, the operator can run
   `python -m mcad reset-password --email name@example.com`; this revokes old sessions.
3. Sign in at `/admin`. In **Team access**, choose a role and create an
   invitation. Copy the single-use link; the app does not send emails.
   Invitations expire after 72 hours and can be revoked while pending.
4. Give reviewers the **Reviewer** role for the candidate queue. They can
   approve or reject incoming reports; administrators also manage team access,
   discovery, Telegram settings and published records.

Site invitations do not grant access to Railway or GitHub. People who need to
manage infrastructure can be invited in **MCAD → Settings → Members** as Railway
editors. Project-level access excludes unrelated projects; it includes this
project's production settings. A separate workspace is unnecessary for that
boundary. Give operators access to a separate test project if they must manage
only test infrastructure.

## Optional admin hostname

The same service can host the workspace at an address such as
`admin.example.com`. A real domain and control of its DNS are required.
Under the MCAD web service's Public Networking settings, add the custom domain
and install the CNAME and TXT records supplied by Railway. In the service's
build variables set `VITE_ADMIN_HOST=admin.example.com` and
`VITE_PUBLIC_ORIGIN=https://mcad.example.com` (substitute the actual map URL),
then rebuild. The admin hostname hosts the same-origin API, so sessions stay
host-only and no cross-origin authentication configuration is needed. The
original `/admin` URL remains a fallback.

For a test preview with the same production records, use a public-read-only
preview or a sanitized copy for the app's test data. Never give a test container
production write credentials: the normal image migrates the configured database
at startup. Full workflow tests can use a separate logical database on one
PostgreSQL server when network and access boundaries allow, with separate
credentials. Don't copy live Telegram tokens or OpenAI keys; keep background
workers and both paid AI switches off by default in test.

## Discovery costs and Telegram feedback

Keep `ENABLE_AI_SEARCH=false` until live research is approved. Saved search
priorities guide open-web discovery rather than restricting it to a source
list. The Discovery page shows run history, tokens, tool calls, and leads screened
before verification. Admins can enter the applicable per-model rates and set
a daily dashboard warning. Costs are estimates, not provider billing; the
warning does not stop spending. Cost per approved incident uses priced runs
started that UTC day and their approved results. Run and tool limits still
constrain requests independently.

Set `TELEGRAM_REVIEW_CHAT_ID` to the numeric ID of a private chat or internal
group, and `TELEGRAM_REVIEWER_IDS` to the comma-separated numeric Telegram user
IDs allowed to moderate there. Add the bot to the chat and enable background
workers and polling. Candidate alerts show the proposed description and sources.
Editors can accept a verified report or reply to its message with `/reject`
and an explanation. The public Telegram feed keeps its link format.

Publication requires sources from at least two independent reporting origins.
Reprints of the same report count as one. In the web queue, editors confirm
the supporting sources before publishing. For a follow-up, compare the proposed
match and choose the existing incident to update. The new facts and references
appear on the original record. A follow-up must have independent support for
its new facts.

When an editor rejects a draft, the original proposal and explanation are
saved together as a confirmed regression case. With `ENABLE_AI_FEEDBACK=true`,
an API key and background workers, the server batches new editorial rejections
into prompt drafts. **Feedback & prompts** also supports an explicit draft request
and evaluation. Case replay uses the saved evidence and editor's explanation
without web tools. An administrator activates a version after all confirmed cases
pass. Previous versions and the built-in prompt are available for rollback.
`ENABLE_AI_FEEDBACK` and `ENABLE_AI_SEARCH` are independent paid features.
