"""Work out how to send and receive mail for an address, from its domain.

`detect("someone@company.com")` looks at well-known consumer domains first, then the
domain's MX records (so company domains hosted on Microsoft 365 or Google Workspace are
recognised), then Mozilla's public autoconfig database (ISPDB) for everything else.
It returns the SMTP/IMAP endpoints and, just as important, which kind of credential each
provider accepts today, so staff get a correct instruction instead of a failed login.

Never raises: unknown or unreachable domains yield a generic profile with a note.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import httpx

log = logging.getLogger("docshealth.mailproviders")


@dataclass
class ProviderProfile:
    key: str
    name: str
    smtp_host: str | None
    smtp_port: int | None
    smtp_security: str | None  # starttls | ssl
    imap_host: str | None = None
    imap_port: int | None = 993
    recommended_outbound: str = "smtp"  # smtp | graph
    recommended_inbound: str = "imap"  # imap | graph | none
    credential: str = "password"
    notes: list[str] = field(default_factory=list)
    mx: str | None = None
    source: str = "builtin"  # builtin | mx | ispdb | guess

    def as_dict(self) -> dict:
        return asdict(self)


def _p(**kw) -> ProviderProfile:
    return ProviderProfile(**kw)


PROFILES: dict[str, ProviderProfile] = {
    "google": _p(key="google", name="Gmail / Google Workspace", smtp_host="smtp.gmail.com", smtp_port=587,
                 smtp_security="starttls", imap_host="imap.gmail.com", credential="app_password",
                 notes=["Turn on 2-Step Verification, then create an App password (Google Account > Security > "
                        "App passwords) and use it instead of the normal password.",
                        "Workspace admins can also block app passwords; then OAuth is required."]),
    "microsoft365": _p(key="microsoft365", name="Microsoft 365 (company domain)", smtp_host="smtp.office365.com",
                       smtp_port=587, smtp_security="starttls", imap_host="outlook.office365.com",
                       recommended_outbound="graph", recommended_inbound="graph", credential="oauth_app",
                       notes=["Best: a Microsoft Graph app registration (tenant ID, client ID, client secret, "
                              "Mail.Send + Mail.ReadWrite application permissions) which works with MFA.",
                              "SMTP with a password only works if an admin enabled Authenticated SMTP for the "
                              "mailbox; Microsoft is retiring password SMTP.",
                              "Password IMAP is disabled in Microsoft 365, so receiving requires Graph."]),
    "outlook_consumer": _p(key="outlook_consumer", name="Outlook.com / Hotmail / Live", smtp_host="smtp-mail.outlook.com",
                           smtp_port=587, smtp_security="starttls", imap_host="outlook.office365.com",
                           recommended_inbound="none", credential="oauth_only",
                           notes=["Microsoft removed password sign-in for SMTP/IMAP on personal Outlook.com "
                                  "accounts; these need OAuth. Use a company mailbox or an email API instead."]),
    "yahoo": _p(key="yahoo", name="Yahoo Mail", smtp_host="smtp.mail.yahoo.com", smtp_port=465, smtp_security="ssl",
                imap_host="imap.mail.yahoo.com", credential="app_password",
                notes=["Generate an app password (Yahoo Account Security > Generate app password)."]),
    "icloud": _p(key="icloud", name="iCloud Mail", smtp_host="smtp.mail.me.com", smtp_port=587, smtp_security="starttls",
                 imap_host="imap.mail.me.com", credential="app_password",
                 notes=["Use an app-specific password from appleid.apple.com."]),
    "zoho": _p(key="zoho", name="Zoho Mail", smtp_host="smtp.zoho.com", smtp_port=587, smtp_security="starttls",
               imap_host="imap.zoho.com", credential="app_password",
               notes=["With 2FA on, create an application-specific password in Zoho Accounts."]),
    "godaddy": _p(key="godaddy", name="GoDaddy Workspace Email", smtp_host="smtpout.secureserver.net", smtp_port=465,
                  smtp_security="ssl", imap_host="imap.secureserver.net", credential="password"),
}

CONSUMER_DOMAINS = {
    "gmail.com": "google", "googlemail.com": "google",
    "outlook.com": "outlook_consumer", "hotmail.com": "outlook_consumer", "live.com": "outlook_consumer",
    "msn.com": "outlook_consumer",
    "yahoo.com": "yahoo", "ymail.com": "yahoo", "rocketmail.com": "yahoo",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "zoho.com": "zoho", "zohomail.com": "zoho",
}

MX_PATTERNS = [
    (re.compile(r"\.mail\.protection\.outlook\.com$"), "microsoft365"),
    (re.compile(r"\.olc\.protection\.outlook\.com$"), "outlook_consumer"),
    (re.compile(r"(^|\.)(google|googlemail)\.com$"), "google"),
    (re.compile(r"(^|\.)yahoodns\.net$"), "yahoo"),
    (re.compile(r"(^|\.)mail\.icloud\.com$"), "icloud"),
    (re.compile(r"(^|\.)zoho\.(com|eu|in)$"), "zoho"),
    (re.compile(r"(^|\.)secureserver\.net$"), "godaddy"),
]


def _copy(key: str, **update) -> ProviderProfile:
    base = PROFILES[key]
    return ProviderProfile(**{**base.as_dict(), "notes": list(base.notes), **update})


def _mx(domain: str) -> str | None:
    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".").lower()
    except Exception as exc:  # NXDOMAIN, no MX, timeout, no DNS: fall through to other methods
        log.info("MX lookup for %s failed: %s", domain, exc)
        return None


def _ispdb(domain: str) -> ProviderProfile | None:
    """Mozilla Thunderbird's public autoconfig database (only the domain name is sent)."""
    try:
        r = httpx.get(f"https://autoconfig.thunderbird.net/v1.1/{domain}", timeout=6)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
        prov = root.find("emailProvider")
        smtp = prov.find("outgoingServer[@type='smtp']")
        imap = prov.find("incomingServer[@type='imap']")
        sock = (smtp.findtext("socketType") or "").upper()
        return ProviderProfile(
            key="ispdb", name=prov.findtext("displayName") or domain, smtp_host=smtp.findtext("hostname"),
            smtp_port=int(smtp.findtext("port") or 587), smtp_security="ssl" if sock == "SSL" else "starttls",
            imap_host=imap.findtext("hostname") if imap is not None else None,
            imap_port=int(imap.findtext("port") or 993) if imap is not None else None,
            notes=["Settings from Mozilla's ISP database; confirm with the provider."], source="ispdb")
    except Exception as exc:  # network error, malformed XML, missing elements
        log.info("ISPDB lookup for %s failed: %s", domain, exc)
        return None


@lru_cache(maxsize=512)
def detect(address: str) -> ProviderProfile:
    domain = (address or "").rsplit("@", 1)[-1].strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return ProviderProfile(key="invalid", name="Invalid address", smtp_host=None, smtp_port=None,
                               smtp_security=None, recommended_inbound="none", notes=["Enter a full email address."],
                               source="guess")
    if domain in CONSUMER_DOMAINS:
        return _copy(CONSUMER_DOMAINS[domain], source="builtin")
    mx = _mx(domain)
    if mx:
        for pattern, key in MX_PATTERNS:
            if pattern.search(mx):
                return _copy(key, mx=mx, source="mx")
    found = _ispdb(domain)
    if found:
        found.mx = mx
        return found
    return ProviderProfile(key="generic", name=f"Other provider ({domain})", smtp_host=f"smtp.{domain}", smtp_port=587,
                           smtp_security="starttls", imap_host=f"imap.{domain}", mx=mx, source="guess",
                           notes=["Couldn't identify the provider; these are common defaults. Ask your email "
                                  "administrator for the SMTP host, port and whether an app password is needed."])
