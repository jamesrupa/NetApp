"""DNS lookup with plain-English explanations of every record, plus a record-type reference.

Looks up A, AAAA, CNAME, MX, NS, TXT, SOA, CAA and HTTPS records (PTR for IP
addresses, SRV for _service._proto names), decodes SPF / DMARC / DKIM, names
well-known providers, and flags common problems (e.g. email spoofing protection).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from urllib.parse import urlparse

import dns.asyncresolver
import dns.exception
import dns.name
import dns.rdatatype
import dns.resolver
import dns.reversename

RESOLVERS = {
    "system": ("Your network's DNS", None),
    "cloudflare": ("Cloudflare 1.1.1.1", ["1.1.1.1", "1.0.0.1"]),
    "google": ("Google 8.8.8.8", ["8.8.8.8", "8.8.4.4"]),
    "quad9": ("Quad9 9.9.9.9", ["9.9.9.9", "149.112.112.112"]),
}
TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA", "HTTPS"]
DKIM_SELECTORS = ["google", "selector1", "selector2", "k1", "k2", "default", "s1", "s2", "dkim", "mail", "fm1", "protonmail"]


class DnsError(ValueError):
    pass


# --- provider hints ---------------------------------------------------------------------

MX_PROVIDERS = [
    (("google.com", "googlemail.com"), "Google Workspace (Gmail)"),
    (("outlook.com", "protection.outlook.com", "office365.us"), "Microsoft 365 (Exchange Online)"),
    (("protonmail.ch", "proton.me"), "Proton Mail"),
    (("zoho.com", "zoho.eu", "zohomail.com"), "Zoho Mail"),
    (("icloud.com", "me.com"), "iCloud Mail"),
    (("messagingengine.com",), "Fastmail"),
    (("mimecast.com",), "Mimecast (email security filter)"),
    (("pphosted.com", "ppe-hosted.com"), "Proofpoint (email security filter)"),
    (("barracudanetworks.com",), "Barracuda (email security filter)"),
    (("secureserver.net",), "GoDaddy email"),
    (("mailgun.org",), "Mailgun"),
    (("amazonaws.com",), "Amazon SES"),
    (("yahoodns.net",), "Yahoo Mail"),
]
NS_PROVIDERS = [
    (("cloudflare.com",), "Cloudflare"),
    (("awsdns",), "Amazon Route 53"),
    (("domaincontrol.com",), "GoDaddy"),
    (("azure-dns",), "Azure DNS"),
    (("googledomains.com", "ns-cloud-", "google.com"), "Google Cloud DNS"),
    (("nsone.net",), "NS1"),
    (("registrar-servers.com",), "Namecheap"),
    (("dnsimple.com",), "DNSimple"),
    (("digitalocean.com",), "DigitalOcean"),
    (("linode.com",), "Linode / Akamai"),
    (("akam.net", "akamaiedge"), "Akamai"),
    (("ultradns",), "UltraDNS"),
    (("dynect.net",), "Oracle Dyn"),
    (("wixdns.net",), "Wix"),
    (("squarespacedns.com",), "Squarespace"),
    (("hostinger",), "Hostinger"),
]
CNAME_PROVIDERS = [
    (("cloudfront.net",), "Amazon CloudFront (CDN)"),
    (("akamaiedge.net", "akamai.net", "edgekey.net", "edgesuite.net"), "Akamai (CDN)"),
    (("fastly.net", "fastlylb.net"), "Fastly (CDN)"),
    (("cdn.cloudflare.net",), "Cloudflare (CDN)"),
    (("azureedge.net", "azurefd.net", "azurewebsites.net", "cloudapp.net", "trafficmanager.net"), "Microsoft Azure"),
    (("github.io",), "GitHub Pages"),
    (("herokudns.com", "herokuapp.com"), "Heroku"),
    (("vercel-dns.com", "vercel.app"), "Vercel"),
    (("netlify.app", "netlify.com"), "Netlify"),
    (("myshopify.com", "shopify.com"), "Shopify"),
    (("squarespace.com",), "Squarespace"),
    (("wixdns.net",), "Wix"),
    (("ghs.googlehosted.com", "googlehosted.com"), "Google (Sites / Workspace)"),
    (("elb.amazonaws.com",), "AWS load balancer"),
]
SPF_INCLUDES = [
    ("_spf.google.com", "Google Workspace"), ("spf.protection.outlook.com", "Microsoft 365"),
    ("sendgrid.net", "SendGrid"), ("mailgun.org", "Mailgun"), ("amazonses.com", "Amazon SES"),
    ("mcsv.net", "Mailchimp"), ("servers.mcsv.net", "Mailchimp"), ("spf.mandrillapp.com", "Mailchimp Transactional"),
    ("zoho", "Zoho Mail"), ("protonmail.ch", "Proton Mail"), ("_spf.salesforce.com", "Salesforce"),
    ("spf.messagingengine.com", "Fastmail"), ("secureserver.net", "GoDaddy"), ("icloud.com", "iCloud"),
    ("hubspotemail.net", "HubSpot"), ("_spf.createsend.com", "Campaign Monitor"), ("zendesk.com", "Zendesk"),
]
TXT_TOKENS = [
    ("google-site-verification=", "Proves ownership of the domain to Google (Search Console / Workspace)."),
    ("MS=", "Proves ownership of the domain to Microsoft 365."),
    ("facebook-domain-verification=", "Proves ownership of the domain to Meta/Facebook."),
    ("apple-domain-verification=", "Proves ownership of the domain to Apple."),
    ("atlassian-domain-verification=", "Proves ownership of the domain to Atlassian (Jira/Confluence)."),
    ("docusign=", "Proves ownership of the domain to DocuSign."),
    ("adobe-idp-site-verification=", "Proves ownership of the domain to Adobe."),
    ("stripe-verification=", "Proves ownership of the domain to Stripe."),
    ("globalsign-domain-verification=", "Proves ownership to the GlobalSign certificate authority."),
    ("onetrust-domain-verification=", "Proves ownership of the domain to OneTrust."),
    ("zoom-domain-verification", "Proves ownership of the domain to Zoom."),
    ("openai-domain-verification=", "Proves ownership of the domain to OpenAI."),
    ("_github-challenge", "Proves ownership of the domain to GitHub."),
]


def _provider(host: str, table) -> str | None:
    host = host.lower().rstrip(".")
    for needles, name in table:
        if any(n in host for n in needles):
            return name
    return None


# --- helpers ----------------------------------------------------------------------------

def human_ttl(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h" + (f" {seconds % 3600 // 60} min" if seconds % 3600 else "")
    return f"{seconds // 86400} day{'s' if seconds >= 172800 else ''}"


def clean_name(text: str) -> str:
    """Accept "example.com", "https://www.example.com/path", "  Example.COM. " and IP addresses."""
    text = (text or "").strip()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/")[0].strip().rstrip(".").lower()
    if not text:
        raise DnsError("Enter a domain name like example.com, or an IP address.")
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not re.fullmatch(r"[a-z0-9_]([a-z0-9_-]{0,62})(\.[a-z0-9_-]{1,63})*", text, re.I) and not text.startswith("xn--"):
        try:
            text = text.encode("idna").decode()  # international names, e.g. bücher.de
        except UnicodeError as exc:
            raise DnsError(f"'{text}' isn't a valid domain name.") from exc
    return text


def parse_spf(txt: str) -> dict:
    """Explain an SPF record term by term."""
    terms, lookups, warnings = [], 0, []
    qual_words = {"+": "allow", "-": "reject", "~": "soft-fail (accept but mark suspicious)", "?": "neutral (no opinion)"}
    final = None
    for raw in txt.split()[1:]:
        q = raw[0] if raw[0] in "+-~?" else "+"
        term = raw[1:] if raw[0] in "+-~?" else raw
        mech, _, arg = term.partition(":")
        mech_l = mech.lower()
        if mech_l == "include":
            lookups += 1
            who = next((name for needle, name in SPF_INCLUDES if needle in arg.lower()), None)
            text = f"Also allow the servers that {arg} lists" + (f" ({who})." if who else ".")
        elif mech_l in ("ip4", "ip6"):
            text = f"Allow mail from {arg}."
        elif mech_l == "a":
            lookups += 1
            text = f"Allow the IP addresses of {arg or 'this domain'} (its A/AAAA records)."
        elif mech_l == "mx":
            lookups += 1
            text = f"Allow {arg or 'this domain'}'s own mail servers (MX)."
        elif mech_l == "all":
            final = q
            text = {
                "-": "Reject mail from every other server. Strict, and the recommended ending.",
                "~": "Mail from any other server is accepted but marked as suspicious (soft fail). Common and acceptable.",
                "?": "No opinion about other servers: SPF gives no protection.",
                "+": "Allow ANY server on the internet to send as this domain: SPF gives no protection.",
            }[q]
        elif mech_l.startswith("redirect="):
            lookups += 1
            text = f"Use {term.split('=', 1)[1]}'s SPF policy instead."
        elif mech_l == "exists":
            lookups += 1
            text = f"Advanced rule: allow if {arg} exists."
        elif mech_l == "ptr":
            lookups += 1
            text = "Allow servers whose reverse DNS matches (deprecated and slow)."
            warnings.append("The 'ptr' mechanism is deprecated (RFC 7208); remove it.")
        else:
            text = "Modifier or unknown term."
        terms.append({"term": raw, "action": qual_words[q] if mech_l != "all" else None, "explanation": text})
    if final == "+":
        warnings.append("'+all' lets anyone send email as this domain. Change it to '~all' or '-all'.")
    elif final == "?":
        warnings.append("'?all' gives no protection. Change it to '~all' or '-all'.")
    elif final is None and not any(t["term"].lower().startswith("redirect=") for t in terms):
        warnings.append("The record has no 'all' ending, so mail from other servers isn't covered.")
    if lookups > 10:
        warnings.append(f"Uses {lookups} DNS lookups; SPF allows at most 10, so receivers may treat it as broken.")
    return {"terms": terms, "lookups": lookups, "policy": final, "warnings": warnings}


def parse_dmarc(txt: str) -> dict:
    tags = {}
    for part in txt.split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            tags[k.strip().lower()] = v.strip()
    policy = tags.get("p", "").lower()
    explain = {
        "none": "Monitoring only: receivers deliver spoofed mail as normal and just send you reports.",
        "quarantine": "Mail that fails the checks goes to spam/junk.",
        "reject": "Mail that fails the checks is refused. Strongest protection against spoofing.",
    }
    lines = [f"Policy (p={policy or '?'}): {explain.get(policy, 'missing or invalid; receivers treat it as none.')}"]
    if "sp" in tags:
        lines.append(f"Subdomains (sp={tags['sp']}): {explain.get(tags['sp'].lower(), tags['sp'])}")
    if tags.get("pct") and tags["pct"] != "100":
        lines.append(f"Only {tags['pct']}% of failing mail gets the policy; the rest is treated as 'none'.")
    if "rua" in tags:
        lines.append(f"Daily summary reports go to {tags['rua'].replace('mailto:', '')}.")
    if "ruf" in tags:
        lines.append(f"Per-message failure reports go to {tags['ruf'].replace('mailto:', '')}.")
    for tag, label in (("adkim", "DKIM"), ("aspf", "SPF")):
        if tag in tags:
            lines.append(f"{label} alignment: {'strict (exact domain match)' if tags[tag] == 's' else 'relaxed (subdomains count)'}.")
    return {"tags": tags, "policy": policy or None, "lines": lines}


def parse_soa_email(rname: str) -> str:
    """hostmaster.example.com. -> hostmaster@example.com (an escaped '\\.' stays a dot)."""
    rname = rname.rstrip(".")
    m = re.match(r"^((?:[^.\\]|\\.)*)\.(.*)$", rname)
    return f"{m.group(1).replace(chr(92) + '.', '.')}@{m.group(2)}" if m else rname


def explain_record(rtype: str, value: str, name: str) -> str:
    v = value.rstrip(".")
    if rtype in ("A", "AAAA"):
        ip = ipaddress.ip_address(v)
        kind = "IPv4" if rtype == "A" else "IPv6"
        extra = " This is a private address, only reachable inside a local network." if ip.is_private else ""
        return f"{kind} address that {name} points to: browsers and apps connect here.{extra}"
    if rtype == "CNAME":
        who = _provider(v, CNAME_PROVIDERS)
        return f"Alias: {name} is another name for {v}, so the lookup continues there." + (f" Hosted on {who}." if who else "")
    if rtype == "MX":
        pref, _, host = v.partition(" ")
        if host in (".", ""):
            return "Null MX: this domain doesn't accept email at all."
        who = _provider(host, MX_PROVIDERS)
        return (f"Mail server {host} (priority {pref}: lower numbers are tried first)."
                + (f" Email is handled by {who}." if who else ""))
    if rtype == "NS":
        who = _provider(v, NS_PROVIDERS)
        return f"Name server answering DNS questions for this domain." + (f" DNS is hosted by {who}." if who else "")
    if rtype == "CAA":
        parts = v.split(" ", 2)
        if len(parts) == 3:
            tag, ca = parts[1], parts[2].strip('"')
            if tag == "issue":
                return f"Only {ca or 'no one'} may issue normal TLS certificates for this domain."
            if tag == "issuewild":
                return f"Only {ca or 'no one'} may issue wildcard (*.domain) certificates."
            if tag == "iodef":
                return f"Certificate authorities report violations to {ca}."
        return "Certificate Authority Authorization rule."
    if rtype == "HTTPS":
        alpn = re.search(r'alpn="?([^"\s]+)', v)
        protos = alpn.group(1).split(",") if alpn else []
        bits = []
        if "h3" in protos:
            bits.append("supports HTTP/3 (QUIC)")
        if "h2" in protos:
            bits.append("supports HTTP/2")
        if "ech=" in v:
            bits.append("supports Encrypted Client Hello (hides the site name from the network)")
        if "ipv4hint" in v or "ipv6hint" in v:
            bits.append("includes address hints to connect faster")
        return "Tells browsers how to connect before they even ask: " + (", ".join(bits) if bits else "service binding parameters") + "."
    if rtype == "PTR":
        return f"Reverse DNS: the name registered for this IP address is {v}."
    if rtype == "SRV":
        p = v.split()
        if len(p) == 4:
            return f"Service runs on {p[3].rstrip('.')} port {p[2]} (priority {p[0]}, weight {p[1]})."
    return ""


def explain_txt(value: str) -> dict:
    v = value.strip('"')
    if v.lower().startswith("v=spf1"):
        return {"kind": "SPF", "explanation": "SPF: lists which servers may send email for this domain.", "spf": parse_spf(v)}
    if v.upper().startswith("V=DMARC1"):
        return {"kind": "DMARC", "explanation": "DMARC policy.", "dmarc": parse_dmarc(v)}
    if v.upper().startswith("V=DKIM1"):
        return {"kind": "DKIM", "explanation": "DKIM public key used to check email signatures."}
    for token, text in TXT_TOKENS:
        if v.startswith(token):
            return {"kind": "Verification", "explanation": text}
    return {"kind": "Text", "explanation": "Free-form text, usually a verification token or a note for some service."}


def explain_soa(value: str) -> list[str]:
    p = value.split()
    if len(p) != 7:
        return []
    mname, rname, serial, refresh, retry, expire, minimum = p
    looks_like_date = re.fullmatch(r"(19|20)\d{6}\d{0,2}", serial)
    date_note = f" (looks like a date: {serial[:4]}-{serial[4:6]}-{serial[6:8]})" if looks_like_date else ""
    return [
        f"Primary name server: {mname.rstrip('.')}",
        f"Administrator: {parse_soa_email(rname)}",
        f"Serial {serial}: bumped on every change{date_note}",
        f"Secondary servers re-check every {human_ttl(int(refresh))}, retry after {human_ttl(int(retry))}, "
        f"and give up after {human_ttl(int(expire))}",
        f"\"Doesn't exist\" answers are cached for {human_ttl(int(minimum))}",
    ]


# --- lookup -----------------------------------------------------------------------------

def _resolver(which: str) -> dns.asyncresolver.Resolver:
    if which not in RESOLVERS:
        raise DnsError(f"Unknown resolver: {which}")
    r = dns.asyncresolver.Resolver()
    servers = RESOLVERS[which][1]
    if servers:
        r.nameservers = servers
    r.lifetime = 5.0
    # Ask for large UDP answers (EDNS): big TXT sets then arrive without falling back to TCP,
    # which some networks block.
    r.use_edns(0, 0, 4096)
    return r


async def _query(r, name: str, rtype: str) -> dict:
    t0 = time.perf_counter()
    try:
        ans = await r.resolve(name, rtype, raise_on_no_answer=False)
    except dns.resolver.NXDOMAIN:
        return {"type": rtype, "status": "nxdomain", "records": []}
    except dns.resolver.NoNameservers as exc:
        return {"type": rtype, "status": "error", "error": "The DNS servers refused or failed the query.", "records": [],
                "detail": str(exc)[:200]}
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        return {"type": rtype, "status": "timeout", "records": []}
    except dns.exception.DNSException as exc:
        return {"type": rtype, "status": "error", "error": str(exc)[:200], "records": []}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    rrset = ans.rrset
    if rrset is None:
        return {"type": rtype, "status": "none", "records": [], "ms": ms}
    out = {"type": rtype, "status": "ok", "ttl": rrset.ttl, "ttl_human": human_ttl(rrset.ttl), "ms": ms, "records": []}
    if ans.canonical_name and str(ans.canonical_name).rstrip(".") != name.rstrip("."):
        out["via_cname"] = str(ans.canonical_name).rstrip(".")
    for rd in rrset:
        if rtype == "TXT":
            value = "".join(s.decode(errors="replace") for s in rd.strings)
            out["records"].append({"value": value, **explain_txt(value)})
            continue
        value = rd.to_text()
        rec = {"value": value.rstrip("."), "explanation": explain_record(rtype, value, name)}
        if rtype == "SOA":
            rec["details"] = explain_soa(value)
            rec["explanation"] = "Start of Authority: who's in charge of this DNS zone and its timers."
        out["records"].append(rec)
    if rtype == "MX":
        out["records"].sort(key=lambda r: int(r["value"].split()[0]) if r["value"].split()[0].isdigit() else 0)
    return out


def org_domains(name: str) -> list[str]:
    """example.com for www.example.com (and the name itself): where DMARC/CAA usually live."""
    labels = name.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)] or [name]


ANSWERED = ("ok", "none", "nxdomain")  # anything else (timeout/error) means "unknown", never "missing"


def dkim_key(value: str) -> bool:
    """True if a DKIM record holds an actual public key (an empty p= means "revoked / sends no mail")."""
    return bool(re.search(r"p=[A-Za-z0-9+/]{16,}", value.replace(" ", "")))


def findings(name: str, results: dict, dmarc: dict | None, dkim: list, dmarc_status: str = "ok",
             dkim_note: str | None = None) -> list[dict]:
    out = []
    mx = results.get("MX", {})
    accepts_mail = mx.get("status") == "ok" and not any(r["explanation"].startswith("Null MX") for r in mx["records"])
    txt = results.get("TXT", {})
    spf = next((r for r in txt.get("records", []) if r.get("kind") == "SPF"), None)
    unknown = [t for t in ("TXT", "MX") if results.get(t, {}).get("status") not in ANSWERED]
    if unknown:
        out.append({"severity": "info", "category": "DNS", "title": f"Couldn't load {' and '.join(unknown)} records",
                    "detail": "The DNS server didn't answer in time, so email checks for those records were skipped. "
                              "Try again or pick another resolver.", "action": ""})
    if (accepts_mail or spf) and txt.get("status") in ANSWERED:
        if not spf:
            out.append({"severity": "warning", "category": "Email", "title": "No SPF record",
                        "detail": "Without SPF, receiving servers can't tell which servers may send email for this domain, "
                                  "which makes it easier to spoof.",
                        "action": "Add a TXT record starting with v=spf1 listing your mail provider (e.g. include:_spf.google.com) and ending in ~all or -all."})
        else:
            for w in spf["spf"]["warnings"]:
                out.append({"severity": "critical" if "+all" in w else "warning", "category": "Email",
                            "title": "SPF record problem", "detail": w, "action": ""})
        if not dmarc and dmarc_status not in ANSWERED:
            pass  # couldn't check
        elif not dmarc:
            out.append({"severity": "warning", "category": "Email", "title": "No DMARC policy",
                        "detail": "DMARC tells receivers what to do with mail that fails SPF/DKIM. Without it, spoofed mail "
                                  "often still gets delivered, and you get no reports about it.",
                        "action": f"Add a TXT record at _dmarc.{org_domains(name)[-1]}: start with "
                                  "v=DMARC1; p=none; rua=mailto:you@yourdomain, then move to p=quarantine or p=reject."})
        elif dmarc["parsed"]["policy"] in (None, "none"):
            out.append({"severity": "info", "category": "Email", "title": "DMARC is in monitoring mode (p=none)",
                        "detail": "Spoofed mail is still delivered; you only receive reports.",
                        "action": "Once the reports look clean, change the policy to p=quarantine, then p=reject."})
        else:
            out.append({"severity": "good", "category": "Email", "title": f"DMARC enforced (p={dmarc['parsed']['policy']})",
                        "detail": "Receivers act on mail that fails authentication.", "action": ""})
        if accepts_mail and not dkim and not dkim_note:
            out.append({"severity": "info", "category": "Email", "title": "No DKIM key found at common selectors",
                        "detail": "DKIM keys live at <selector>._domainkey.<domain>, and the selector name varies by provider, "
                                  "so this check can't be certain. Check your email provider's DKIM settings.", "action": ""})
    if results.get("CAA", {}).get("status") in ("none", "nxdomain") and results.get("A", {}).get("status") == "ok":
        out.append({"severity": "info", "category": "Certificates", "title": "No CAA record",
                    "detail": "Any certificate authority may issue TLS certificates for this domain.",
                    "action": "Optional hardening: add CAA records naming only the CA(s) you use, e.g. 0 issue \"letsencrypt.org\"."})
    ns = results.get("NS", {})
    if ns.get("status") == "ok" and len(ns["records"]) == 1:
        out.append({"severity": "warning", "category": "DNS", "title": "Only one name server",
                    "detail": "If it goes down, the whole domain stops resolving.", "action": "Use at least two name servers."})
    return out


async def lookup(query: str, resolver: str = "system") -> dict:
    name = clean_name(query)
    r = _resolver(resolver)
    started = time.perf_counter()
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        ip = None
    if ip is not None:
        ptr_name = dns.reversename.from_address(name).to_text()
        res = await _query(r, ptr_name, "PTR")
        return {"query": name, "kind": "ip", "resolver": RESOLVERS[resolver][0], "reverse_name": ptr_name.rstrip("."),
                "results": {"PTR": res}, "findings": [], "seconds": round(time.perf_counter() - started, 2)}

    types = list(TYPES)
    if name.startswith("_"):
        types = ["SRV", "TXT", "CNAME"]
    results = dict(zip(types, await asyncio.gather(*(_query(r, name, t) for t in types))))
    if all(v["status"] == "nxdomain" for v in results.values()):
        return {"query": name, "kind": "domain", "resolver": RESOLVERS[resolver][0], "exists": False,
                "results": results, "findings": [], "seconds": round(time.perf_counter() - started, 2)}

    # DMARC lives at _dmarc.<domain>; for subdomains, the organisational domain's record applies.
    dmarc, dmarc_status = None, "none"
    for d in org_domains(name)[:3]:
        res = await _query(r, f"_dmarc.{d}", "TXT")
        if res["status"] not in ANSWERED:
            dmarc_status = res["status"]
        rec = next((x for x in res.get("records", []) if x.get("kind") == "DMARC"), None)
        if rec:
            dmarc, dmarc_status = {"name": f"_dmarc.{d}", "value": rec["value"], "parsed": rec["dmarc"]}, "ok"
            break
    dkim_hits, dkim_note = [], None
    if results.get("MX", {}).get("status") == "ok" or results.get("TXT", {}).get("status") == "ok":
        # A catch-all (*._domainkey) record would make every selector "match": probe a made-up one first.
        probe = await _query(r, f"subnetry-probe-{int(time.time())}._domainkey.{name}", "TXT")
        wildcard = probe["status"] == "ok"
        if wildcard and not any(dkim_key(x["value"]) for x in probe["records"]):
            dkim_note = "This domain publishes an empty DKIM key for every selector, which declares that it sends no email."
        elif not wildcard:
            txts, cnames = await asyncio.gather(
                asyncio.gather(*(_query(r, f"{s}._domainkey.{name}", "TXT") for s in DKIM_SELECTORS)),
                asyncio.gather(*(_query(r, f"{s}._domainkey.{name}", "CNAME") for s in DKIM_SELECTORS)))
            for sel, t, c in zip(DKIM_SELECTORS, txts, cnames):
                if any(dkim_key(x["value"]) for x in t.get("records", [])) or c["status"] == "ok":
                    dkim_hits.append(sel)
    return {
        "query": name, "kind": "domain", "exists": True, "resolver": RESOLVERS[resolver][0],
        "results": results, "dmarc": dmarc, "dkim_selectors": dkim_hits, "dkim_note": dkim_note,
        "findings": findings(name, results, dmarc, dkim_hits, dmarc_status, dkim_note),
        "seconds": round(time.perf_counter() - started, 2),
    }


# --- record-type reference ------------------------------------------------------------------

EXPLAINERS = [
    {"type": "A", "title": "Address (IPv4)", "summary": "Maps a name to an IPv4 address.",
     "example": "example.com.  300  IN  A  93.184.216.34",
     "details": "The most common record: when you visit a site, your device asks for its A record and connects to that "
                "address. A name can have several A records; clients pick one, which spreads the load."},
    {"type": "AAAA", "title": "Address (IPv6)", "summary": "Maps a name to an IPv6 address.",
     "example": "example.com.  300  IN  AAAA  2606:2800:220:1::248",
     "details": "The IPv6 version of A ('quad-A' because IPv6 addresses are four times longer). Devices with IPv6 "
                "connectivity usually prefer it."},
    {"type": "CNAME", "title": "Canonical name (alias)", "summary": "Makes one name an alias of another.",
     "example": "www.example.com.  3600  IN  CNAME  example.com.",
     "details": "The resolver follows the alias and returns the target's records. Popular for pointing a subdomain at a "
                "hosting provider or CDN. A name with a CNAME can't have any other records, which is why the bare "
                "domain (example.com) usually can't be a CNAME."},
    {"type": "MX", "title": "Mail exchanger", "summary": "Says which servers receive email for the domain.",
     "example": "example.com.  3600  IN  MX  10 mail.example.com.",
     "details": "Each MX has a priority: senders try the lowest number first and fall back to higher ones. "
                "A 'null MX' (0 .) means the domain accepts no email."},
    {"type": "NS", "title": "Name server", "summary": "Lists the servers that hold the domain's DNS records.",
     "example": "example.com.  86400  IN  NS  ns1.provider.net.",
     "details": "Set at your registrar. Changing NS records moves your whole DNS to another provider (e.g. to Cloudflare). "
                "Use at least two for redundancy."},
    {"type": "TXT", "title": "Text", "summary": "Free-form text, mostly used for verification and email security.",
     "example": "example.com.  3600  IN  TXT  \"v=spf1 include:_spf.google.com ~all\"",
     "details": "Holds SPF policies, domain-ownership tokens for Google/Microsoft/etc., DKIM keys and DMARC policies."},
    {"type": "SPF", "title": "Sender Policy Framework (a TXT record)", "summary": "Which servers may send email as your domain.",
     "example": "\"v=spf1 include:_spf.google.com ip4:203.0.113.5 -all\"",
     "details": "Receivers check the sending server against this list. End with -all (reject others) or ~all (mark them "
                "suspicious). Never use +all. Limit: 10 DNS lookups (each include/a/mx counts)."},
    {"type": "DKIM", "title": "DomainKeys Identified Mail (a TXT record)", "summary": "Public key used to verify email signatures.",
     "example": "google._domainkey.example.com.  IN  TXT  \"v=DKIM1; k=rsa; p=MIIBIjANBg…\"",
     "details": "Your mail provider signs every outgoing message; receivers fetch the key from "
                "<selector>._domainkey.<domain> to check the signature, proving the mail wasn't forged or altered."},
    {"type": "DMARC", "title": "DMARC policy (a TXT record)", "summary": "Tells receivers what to do when SPF/DKIM fail, and where to send reports.",
     "example": "_dmarc.example.com.  IN  TXT  \"v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com\"",
     "details": "p=none only monitors, p=quarantine sends failures to spam, p=reject blocks them. Start with none, read the "
                "reports, then tighten. Together with SPF and DKIM it stops others spoofing your domain."},
    {"type": "SOA", "title": "Start of authority", "summary": "Administrative info about the zone.",
     "example": "example.com.  IN  SOA  ns1.provider.net. hostmaster.example.com. 2026100301 7200 3600 1209600 300",
     "details": "Names the primary name server and admin email (first dot = @), a serial number that increases on every "
                "change, timers for secondary servers, and how long 'doesn't exist' answers are cached."},
    {"type": "PTR", "title": "Pointer (reverse DNS)", "summary": "Maps an IP address back to a name.",
     "example": "34.216.184.93.in-addr.arpa.  IN  PTR  example.com.",
     "details": "Stored under the reversed address in in-addr.arpa (IPv4) or ip6.arpa (IPv6), and controlled by whoever "
                "owns the IP block (usually your ISP or host). Mail servers check it to fight spam."},
    {"type": "SRV", "title": "Service locator", "summary": "Says which host and port provide a service.",
     "example": "_sip._tcp.example.com.  IN  SRV  10 60 5060 sip.example.com.",
     "details": "Format: priority, weight, port, target. Used by SIP phones, Microsoft Teams/Skype, XMPP chat, Minecraft "
                "and Active Directory. Query names start with _service._protocol."},
    {"type": "CAA", "title": "Certification Authority Authorization", "summary": "Limits which CAs may issue TLS certificates.",
     "example": "example.com.  IN  CAA  0 issue \"letsencrypt.org\"",
     "details": "Certificate authorities must check CAA before issuing. It stops a different CA from (mis)issuing a "
                "certificate for your domain."},
    {"type": "HTTPS", "title": "HTTPS service binding (SVCB)", "summary": "Connection hints for browsers.",
     "example": "example.com.  IN  HTTPS  1 . alpn=\"h3,h2\" ipv4hint=93.184.216.34",
     "details": "Lets a browser learn that a site supports HTTP/3 or Encrypted Client Hello before connecting, saving a "
                "round trip. Added by CDNs such as Cloudflare automatically."},
    {"type": "TTL", "title": "Time to live", "summary": "How long resolvers may cache an answer.",
     "example": "example.com.  300  IN  A  …   (300 s = 5 minutes)",
     "details": "Long TTLs (hours) make lookups faster and reduce load; short TTLs (minutes) make changes take effect "
                "quickly. Lower the TTL a day before migrating a site, then raise it again afterwards."},
    {"type": "DNSSEC", "title": "DNSSEC (DS / DNSKEY / RRSIG)", "summary": "Cryptographic signatures that prove answers are genuine.",
     "example": "example.com.  IN  DS  370 13 2 BE74359954660069D5C632…",
     "details": "The zone signs its records (RRSIG) with keys (DNSKEY); the parent zone vouches for those keys (DS). "
                "Validating resolvers then reject forged answers, preventing DNS spoofing."},
]
