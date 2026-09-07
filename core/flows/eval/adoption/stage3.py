"""Close stage 3 honestly, and correct stage 1.

STAGE 1 was undercounted: `offered` was computed from the first two agent turns only, while the
offer often lands in the third — so a persona that demonstrably ASKED was recorded as never
having been offered anything, which is impossible. Recomputed here over every agent turn in the
saved transcript, from the same logs.

STAGE 3 could not happen by construction: the product's route is "forward the calendar invite to
<mailbox>", and a SIMULATED person cannot forward an email. The funnel also watched the wrong
lane — the poller writes to the founder's lane, not the sim's. So the harness now does what the
person said they would do: sends a REAL ICS to the address THE AGENT NAMED, over the same SMTP
the product uses, and then looks for the invite.received the poller admits. That measures the
product's route rather than the persona's imagination.
"""
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from email.message import EmailMessage

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r4"))
SMTP = ("127.0.0.1", 1025)


def agent_text(case):
    return " ".join(x.get("text") or "" for x in case["log"] if x["who"] == "agent")


OFFER = re.compile(r"your own meeting|meetings you run|be in your|invite .*vexa|forward the "
                   r"(calendar )?invite|vexa@|add (me|vexa) to", re.I)


def send_invite(to_addr, organizer, title, start_epoch):
    uid = f"sim-secondinvite-{int(start_epoch)}-{organizer.split('@')[0]}"
    dt = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch))
    end = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch + 1800))
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
           f"UID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{dt}\r\nDTEND:{end}\r\n"
           f"ORGANIZER;CN={organizer}:mailto:{organizer}\r\n"
           f"ATTENDEE;CN=Vexa:mailto:{to_addr}\r\n"
           f"ATTENDEE;CN=Colleague:mailto:teammate-{organizer}\r\n"
           "LOCATION:https://meet.jit.si/" + uid.replace("-", "")[:30] + "\r\n"
           "DESCRIPTION:https://meet.jit.si/" + uid.replace("-", "")[:30] + "\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    m = EmailMessage()
    m["From"] = organizer
    m["To"] = to_addr
    m["Subject"] = f"Invitation: {title}"
    m.set_content("Forwarding this so Vexa joins.")
    m.add_attachment(ics.encode(), maintype="text", subtype="calendar",
                     filename="invite.ics", params={"method": "REQUEST"})
    with smtplib.SMTP(*SMTP, timeout=20) as s:
        s.send_message(m)
    return uid


def lane_invites(db):
    u = subprocess.run(["bash", "-lc",
                        f'psql "$(sed "s#postgresql+psycopg#postgresql#" ~/.storm/dburl'
                        f' | sed "s#/flows\\$#/{db}#")" -tAc '
                        f'"select source_event_id from reaction where source_event_id like '
                        f"'%secondinvite%'\""], capture_output=True, text=True).stdout
    return [x for x in u.split("\n") if x.strip()]


def main():
    path = f"{RUN}/funnel-production.json"
    cases = json.load(open(path))
    print("=== stage 1 recomputed over EVERY agent turn ===")
    for c in cases:
        t = agent_text(c)
        c["offered_chat"] = bool(OFFER.search(t))
        c["offered"] = c["offered_chat"] or c["offered_mail"]
        print(f"  {c['persona']:28s} offered={c['offered']!s:5s} asked={c['asked']!s:5s} "
              f"route={c['route_given']!s:5s}")

    before_f, before_s = lane_invites("flows"), lane_invites("flows_sim")
    sent = []
    for c in cases:
        if not (c["asked"] and c["route_given"]):
            continue
        addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", agent_text(c))
        if not addr:
            continue
        to = addr.group(0).rstrip(".*_)")
        uid = send_invite(to, c["email"], "Show A Animation dailies — my own", time.time() + 86400)
        sent.append((c["persona"], to, uid))
        print(f"\n  FORWARDED as {c['persona']}: real ICS -> {to}  (uid {uid})")

    if not sent:
        print("\n  nobody asked AND was given a route — nothing to forward")
    else:
        print("  waiting for the poller…")
        for _ in range(30):
            time.sleep(10)
            if len(lane_invites("flows")) > len(before_f) or \
               len(lane_invites("flows_sim")) > len(before_s):
                break
    after_f, after_s = lane_invites("flows"), lane_invites("flows_sim")
    print(f"\n=== STAGE 3 — HAPPENED ===")
    print(f"  invites forwarded (as the person)      : {len(sent)}")
    print(f"  invite.received, founder lane `flows`  : {len(before_f)} -> {len(after_f)}")
    print(f"  invite.received, sim lane `flows_sim`  : {len(before_s)} -> {len(after_s)}")
    for x in after_f:
        print(f"    {x}")
    json.dump(cases, open(path, "w"), indent=1)
    n = len(cases)
    print(f"\n=== FUNNEL (production, n={n}) ===")
    print(f"  OFFERED  {sum(c['offered'] for c in cases)}/{n}")
    print(f"  ASKED    {sum(c['asked'] for c in cases)}/{n}")
    print(f"  ROUTE    {sum(c['route_given'] for c in cases)}/{n}")
    print(f"  HAPPENED {len(after_f) - len(before_f)}/{len(sent) or 0}")


if __name__ == "__main__":
    sys.exit(main())
