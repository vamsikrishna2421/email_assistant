# What stands between this and other people using it

Engineering is not the hard part of shipping Jerry. These are, roughly in order
of how long they take to clear.

## 1. Google will not let you read strangers' mail without an audit

Gmail read scopes are **restricted**, Google's strictest tier. To publish an app
that uses them you need:

- a verified OAuth consent screen (branding, a privacy policy at a domain you
  control, a demo video), and
- for restricted scopes, an **annual third-party security assessment** — a real
  audit by a Google-approved assessor, historically costing somewhere in the low
  tens of thousands of dollars per year for small vendors.

Until that is done the app is capped at **100 test users**, each added by hand in
the Google Cloud console, and **their refresh tokens expire every 7 days**.

This is the single biggest constraint on Jerry as a product, and it is worth
deciding early because it shapes the architecture:

| Route | What it means | Cost |
|---|---|---|
| **Bring your own credentials** | Each user creates their own Google Cloud project and pastes a client ID. No verification needed — every user is their own developer. Ugly, but honest and free, and some privacy-minded users prefer it. | Free |
| **Verified + assessed** | The normal consumer experience. Sign in with Google, done. | Audit fees, annually |
| **Sidestep Google** | IMAP with an app password. No OAuth, no verification, works with any provider. Users must enable 2FA and generate an app password — a handful of steps, but ones *they* control. | Free |
| **Workspace-only** | Internal-use apps inside a Workspace org skip verification entirely. | Free, but not consumer |

A pragmatic path: **ship with IMAP first**, add verified Google OAuth once there
are enough users to justify the audit. IMAP also makes Jerry work with Outlook,
Fastmail, Proton Bridge and self-hosted mail on day one, which OAuth-per-provider
never would.

## 2. macOS will refuse to run an unsigned app

Downloaded apps are quarantined. Without signing and notarisation the user gets
"Jerry is damaged and can't be opened" — which reads as a broken download, not a
security prompt, so they will not work around it.

Needed: an **Apple Developer account** ($99/year), a Developer ID certificate,
`codesign` on the bundle, then `notarytool` submission and stapling. Roughly a
day of setup, then a build-script step.

## 3. The model download is 4.7 GB

First launch pulls the model. That is a long wait on a slow connection and
several dollars of bandwidth if you ever host a mirror.

Options: ship the smallest tier by default and offer better ones later; download
in the background while the user connects their mail; or make the first digest
run on a smaller model so something useful happens within minutes.

## 4. Nothing is encrypted at rest yet

`~/Library/Application Support/Jerry/jerry.sqlite3` holds live OAuth refresh
tokens in plain text, protected only by file permissions. For a personal tool
that matches what n8n did. For a shipped product the tokens belong in the
**macOS Keychain**, which is a small change and should happen before any build
leaves this machine.

## 5. Restricted scopes forbid some things outright

Google's policy bars using Gmail data to train models and requires that data
stay on the device unless disclosed. Jerry is already local-only, which makes
this easy to comply with — and is worth saying loudly in the listing, because it
is a genuine differentiator rather than a checkbox.

---

## Recommended order

1. **IMAP connector** alongside Gmail OAuth — removes the audit from the critical
   path and multiplies the addressable users.
2. **Keychain** for tokens — before any distributable build exists.
3. **Sign and notarise** — the point at which other people can install it.
4. **Google verification** — only once the user count justifies the audit fee.
