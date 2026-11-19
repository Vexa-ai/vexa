#!/usr/bin/env python3
"""Station 2 — drop a DNA TSC invite into the mail double as the founder (organizer).
SMTP to mailpit (127.0.0.1:1025), To: the mailbox the poller answers as. Nothing leaves the box."""
import argparse, smtplib, time, uuid, calendar
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ap = argparse.ArgumentParser()
ap.add_argument("--to", default="vexa@storm.test")
ap.add_argument("--organizer", default="admin@vexa.ai")
ap.add_argument("--organizer-name", default="Dmitry Grankin")
ap.add_argument("--title", default="ASWF DNA TSC")
ap.add_argument("--minutes-ahead", type=int, default=20)
ap.add_argument("--zoom", default="https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH")
ap.add_argument("--attendee", action="append", default=[], help="Name <addr>")
ap.add_argument("--group", default="")   # e.g. dna-tsc → '#group:dna-tsc' in DESCRIPTION
ap.add_argument("--smtp", default="127.0.0.1:1025")
a = ap.parse_args()

start = int(time.time()) + a.minutes_ahead * 60
fmt = lambda t: time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))
uid = f"dna-tsc-{fmt(start)}@zoom.us"
att_lines = []
for s in a.attendee:
    name, addr = s.rsplit("<", 1); addr = addr.rstrip(">").strip(); name = name.strip()
    att_lines.append(f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;CN={name}:mailto:{addr}")
att_lines.append(f"ATTENDEE;CN=Vexa;PARTSTAT=NEEDS-ACTION:mailto:{a.to}")
desc = f"Join Zoom Meeting{' #group:' + a.group if a.group else ''}\\n{a.zoom}"
ics = "\r\n".join([
    "BEGIN:VCALENDAR", "PRODID:-//Zoom//Zoom Calendar//EN", "VERSION:2.0", "METHOD:REQUEST",
    "BEGIN:VEVENT", f"DTSTART:{fmt(start)}", f"DTEND:{fmt(start + 3600)}", f"UID:{uid}",
    f"DTSTAMP:{fmt(time.time())}",
    f"ORGANIZER;CN={a.organizer_name}:mailto:{a.organizer}", *att_lines,
    f"SUMMARY:{a.title}", f"DESCRIPTION:{desc}", f"LOCATION:{a.zoom}",
    "END:VEVENT", "END:VCALENDAR", ""])

m = EmailMessage()
m["From"] = f"{a.organizer_name} <{a.organizer}>"
m["To"] = a.to
m["Subject"] = f"Invitation: {a.title} @ {time.strftime('%a %b %-d, %Y %H:%M', time.gmtime(start))} (UTC)"
m["Date"] = formatdate(usegmt=True)
m["Message-ID"] = make_msgid(domain="vexa.ai")
m.set_content(f"You have been invited to {a.title}.\n{a.zoom}\n")
m.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics",
                 params={"method": "REQUEST", "charset": "utf-8"})
host, port = a.smtp.split(":")
with smtplib.SMTP(host, int(port)) as s:
    s.send_message(m)
print(f"dropped uid={uid} start={fmt(start)} to={a.to} organizer={a.organizer} attendees={len(a.attendee)} msgid={m['Message-ID']}")
