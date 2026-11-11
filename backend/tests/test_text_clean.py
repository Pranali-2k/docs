"""Email bodies are stored as readable text: no CSS, MSO markup, entities or image tags."""
from app.services.text_clean import clean_body, html_to_text, looks_like_html

OUTLOOK = """<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8"><meta name="Generator" content="Microsoft Word 15 (filtered medium)"><style>
<!--
@font-face {font-family:Aptos}
p.MsoNormal, li.MsoNormal, div.MsoNormal {margin:0in; font-size:12.0pt; font-family:"Aptos",sans-serif}
@page WordSection1 {margin:1.0in 1.0in 1.0in 1.0in}
-->
</style><!--[if gte mso 9]><xml><o:shapedefaults v:ext="edit" spidmax="1026" /></xml><![endif]--></head>
<body lang="EN-US" link="blue"><div class="WordSection1">
<p class="MsoNormal">Please schedule it for 12/21 at 10&nbsp;am.<o:p></o:p></p>
<p class="MsoNormal">&nbsp;</p>
<ul type="disc"><li class="MsoListParagraph">Jane Q. Sample, MD at Example Internal Medicine</li></ul>
<p class="MsoNormal"><span style="font-family:Ebrima">First &amp; Last name: Pat Example</span></p>
<p class="MsoNormal"><span style="font-family:Ebrima">DOB 01/02/1960<br>Phone 555-0100</span></p>
<p><a href="https://protect.example/v2/___wrapped___" originalsrc="https://files.example.com/r/abc">https://files.example.com/r/abc</a></p>
<p><a href="mailto:para@lawfirm.example">para@lawfirm.example</a></p>
<p><img src="cid:image002.jpg@01DD55A5" width="153"></p>
<div style="border-top:solid #E1E1E1 1.0pt"><p><b>From:</b> Para Legal &lt;para@lawfirm.example&gt;<br><b>Sent:</b> Tuesday, October 6, 2026 12:48 PM</p></div>
</div></body></html>"""


def test_outlook_html_becomes_clean_text():
    text = clean_body(OUTLOOK)
    for junk in ("@font-face", "MsoNormal", "WordSection1", "<p>", "<p ", "<span", "<img", "</", "&nbsp;", "&amp;", "cid:", "mso", "font-family"):
        assert junk not in text, junk
    assert "Please schedule it for 12/21 at 10 am." in text
    assert "• Jane Q. Sample, MD at Example Internal Medicine" in text
    assert "First & Last name: Pat Example" in text
    assert "DOB 01/02/1960\nPhone 555-0100" in text  # <br> kept as a line break
    assert "From: Para Legal <para@lawfirm.example>\nSent: Tuesday" in text
    assert "https://files.example.com/r/abc" in text and "protect.example" not in text
    assert "\n\n\n" not in text


def test_plain_text_is_untouched_apart_from_whitespace():
    body = "Hi,\r\nI need to reschedule.  \r\n\r\n\r\n\r\nThanks <3, Ana"
    assert clean_body(body) == "Hi,\nI need to reschedule.\n\nThanks <3, Ana"
    assert not looks_like_html(body)


def test_broken_markup_never_raises():
    assert "hello" in html_to_text("<div><p>hello<span")
    assert clean_body(None) == "" and clean_body("") == ""


def test_intake_stores_clean_text(client):
    from app.config import get_settings

    s = get_settings()
    tok = client.post("/api/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password}).json()
    client.headers["Authorization"] = f"Bearer {tok['access_token']}"
    r = client.post("/api/intake/email", json={"from_address": "html@example.com", "subject": "appt", "body": OUTLOOK})
    assert r.status_code == 201
    inbound = [e for e in r.json()["request"]["emails"] if e["direction"] == "inbound"][0]
    assert "@font-face" not in inbound["body_text"] and "Please schedule it for 12/21" in inbound["body_text"]
