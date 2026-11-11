# Email access needed for the Docs Health scheduling portal

The portal needs a mailbox to **send** emails (appointment confirmations, replies to patients,
password-reset links) and to **receive** emails (patients' scheduling requests, which the AI
turns into portal requests). `ashunya.com` mail is on **Microsoft 365**.

## Option A (recommended): Microsoft Graph app registration, for sending and receiving

Ask the Microsoft 365 / Entra ID administrator for:

1. **Mailbox address** the portal should use, e.g. a shared mailbox `scheduling@ashunya.com`
   (or `pranali@ashunya.com` for testing).
2. An **app registration** in Entra ID (Azure AD) with:
   - **Tenant ID**
   - **Client (application) ID**
   - **Client secret** (value) and its expiry date
3. **API permissions (Application, not Delegated)** on Microsoft Graph, with **admin consent granted**:
   - `Mail.Send` (send)
   - `Mail.ReadWrite` (read new mail and mark it as read)
4. An **Application Access Policy** in Exchange Online that limits the app to that one mailbox only:
   `New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId <mailbox> -AccessRight RestrictAccess`

The portal settings are `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`,
`SCHEDULING_MAILBOX`, `EMAIL_INBOUND_MODE=graph` and `EMAIL_OUTBOUND_MODE=graph`.

This works with MFA and doesn't rely on password-based sign-in, which Microsoft is retiring.

## Option B: SMTP, for sending only (quick test)

1. **Mailbox address** and its **password**. If the account uses MFA, an **app password** instead.
2. The admin must **enable Authenticated SMTP** for that mailbox:
   `Set-CASMailbox -Identity <mailbox> -SmtpClientAuthenticationDisabled $false`
   and confirm that Security Defaults or Conditional Access doesn't block SMTP AUTH for it.
   Microsoft is retiring password (Basic) SMTP AUTH, so ask whether it's still allowed in the tenant.
3. If the "From" address differs from the login mailbox: **Send As** permission on that address.

Server details (already confirmed reachable from the portal): `smtp.office365.com`, port `587`, STARTTLS.

SMTP can only send. To **receive** patients' emails into the portal, Option A (Graph) is needed,
because Microsoft 365 no longer allows password-based IMAP.

## Option C: an email API service (no company mailbox password needed)

Any one of these works over HTTPS and needs only an **API key** plus a **verified sender address or domain**:

| Service | What to provide | Notes |
|---|---|---|
| SendGrid | API key with "Mail Send" permission; verified sender (e.g. `pranali@ashunya.com`) | Single-sender verification takes one confirmation email |
| Mailgun | API key, sending domain (e.g. `mg.ashunya.com`), region US or EU | Domain needs DNS records (SPF/DKIM) |
| Amazon SES | SMTP credentials (`email-smtp.<region>.amazonaws.com`) and a verified identity | Use the SMTP method; HIPAA-eligible with an AWS BAA |

For patient data (PHI), use a provider that signs a **BAA**: Microsoft 365 or Amazon SES, for example.

## Other mailbox providers (e.g. staff Gmail for testing)

Gmail / Google Workspace, Yahoo, Zoho and iCloud: the mailbox address plus an **app password**. 2-step
verification must be on; the account's security page generates the app password. These work for sending
(SMTP) and receiving (IMAP). In Settings, click **Detect settings** and the portal fills in the servers itself.

## How the details are entered

The admin types them in the portal under **Settings → Email delivery** (or `scripts/configure_smtp.py`).
A test email is sent first, and the settings are saved only if Microsoft 365 accepts it. Secrets are stored
only in the server's `.env` file and are never displayed again.
