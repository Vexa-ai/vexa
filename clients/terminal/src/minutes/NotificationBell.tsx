"use client";
/** THE BELL, in the rail's header (#1783): how many notifications are unread, and the list of them.
 *
 *  Opening the list marks what it shows read (the person has now seen them); "Mark all read" clears
 *  the rest. A row opens its object — the chat, the workspace, an invite's join page. The count is a
 *  calm accent pill, never red: being added to a chat is news, not an alarm.
 *
 *  The live half (toasts, the socket) is `notifications.ts`; this only draws the shared store. */
import { RefChip } from "../ui-kit/RefChip";
import { splitRefs } from "../ui-kit/universalRefs";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Bell } from "lucide-react";
import { IconButton, Popover } from "../ui-kit";
import { alertsAskable, answerAlerts, getNotifications, markRead, openNotice, relTime, sortForList, startNotifications, subscribeNotifications, type Notice } from "./notifications";
import { NoticeActs } from "./NoticeActs";
import { useFeature } from "../platform/features";
import { readableSentence } from "./callsList";

export function useNotifications() {
  useEffect(() => startNotifications(), []);
  return useSyncExternalStore(subscribeNotifications, getNotifications, getNotifications);
}

export function NoticeRow({ n, onOpen, onActed }: { n: Notice; onOpen: (n: Notice) => void; onActed?: () => void }) {
  return (
    <div className="vx-notice-item" data-notice-item={n.id}>
      <button type="button" className="vx-notice-row" data-notice={n.kind} data-unread={n.read ? undefined : ""}
        onClick={() => onOpen(n)}>
        <span aria-hidden className="vx-notice-dot" />
        <span className="vx-notice-text">{splitRefs(readableSentence(n.text)).map((x, i) => x.kind === "text" ? x.text : <RefChip key={i} text={x.text} />)}</span>
        <span className="vx-notice-when">{relTime(n.created * 1000)}</span>
      </button>
      <NoticeActs n={n} onDone={onActed} />
    </div>
  );
}

/** Hidden while `notifications` is switched off (`platform/features`); nothing is read then. */
export function NotificationBell() {
  return useFeature("notifications") ? <NotificationBellBody /> : null;
}

function NotificationBellBody() {
  const st = useNotifications();
  const [open, setOpen] = useState(false);
  const closedAt = useRef(0);
  // what was unread when the list opened stays under "New" while it is open, though it is now read
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const toggle = () => {
    // the press that closed the list from outside is this same press on the bell: it stays closed
    if (!open && Date.now() - closedAt.current < 300) return;
    const next = !open;
    setOpen(next);
    // what the list shows on opening is what the person has now seen
    if (next) {
      const ids = st.items.filter((n) => !n.read).slice(0, 30).map((n) => n.id);
      setFresh(new Set(ids));
      if (ids.length) void markRead(ids);
    }
  };
  const label = st.unread ? `Notifications, ${st.unread} unread` : "Notifications";
  const sorted = sortForList(st.items.map((n) => (fresh.has(n.id) ? { ...n, read: false } : n)));
  const neu = sorted.filter((n) => !n.read), earlier = sorted.filter((n) => n.read);
  const real = (n: Notice) => st.items.find((x) => x.id === n.id) ?? n;
  const ask = alertsAskable();
  const row = (n: Notice) => <NoticeRow key={n.id} n={real(n)} onOpen={(x) => { setOpen(false); openNotice(x); }} />;
  return (
    <span className="vx-menu-anchor vx-notice-anchor">
      <IconButton label={label} aria-haspopup="dialog" aria-expanded={open} data-notice-bell="" onClick={toggle}>
        <Bell size={16} strokeWidth={1.75} />
        {st.unread > 0 && <span className="vx-notice-count" data-notice-count={st.unread} aria-hidden>{st.unread > 99 ? "99+" : st.unread}</span>}
      </IconButton>
      {/* PORTALLED TO THE PAGE (founder, live: the bell showed "1" and "I can't open it in any way").
          The rail is a size container (`container-type: inline-size`), which makes it the containing
          block of a `position: fixed` child — the list opened inside the 248px rail, clipped by it, or
          off-screen with the drawer. Drawn on <body>, it sits where the stylesheet puts it. */}
      {typeof document !== "undefined" && createPortal(<span className="vx-notice-anchor vx-notice-portal">
      <Popover open={open} onClose={() => { closedAt.current = Date.now(); setOpen(false); }} label="Notifications">
        <div className="vx-notice-pop">
          <div className="vx-notice-pop-head">
            <span className="vx-notice-pop-title">Notifications</span>
            <button type="button" className="vx-notice-all" data-notice-all="" disabled={!st.items.some((n) => !n.read) && st.unread === 0}
              onClick={() => void markRead("all")}>Mark all read</button>
          </div>
          {ask && <div className="vx-notice-ask" data-notice-ask="">
            <span className="vx-notice-ask-text">Get an alert when this tab is in the background?</span>
            <button type="button" className="vx-notice-act" data-primary="" onClick={() => void answerAlerts(true)}>Turn on</button>
            <button type="button" className="vx-notice-all" onClick={() => void answerAlerts(false)}>Not now</button>
          </div>}
          {st.items.length === 0
            ? <div className="vx-notice-empty" data-notice-empty="">
                <p className="vx-notice-empty-title">You're all caught up</p>
                <p className="vx-notice-empty-sub">When someone shares a workspace, a chat or a meeting with you, it shows here.</p>
              </div>
            : <div className="vx-notice-list">
                {neu.length > 0 && <section aria-label="New"><h3 className="vx-notice-group">New</h3>{neu.map(row)}</section>}
                {earlier.length > 0 && <section aria-label="Earlier"><h3 className="vx-notice-group">Earlier</h3>{earlier.map(row)}</section>}
              </div>}
        </div>
      </Popover>
      </span>, document.body)}
    </span>
  );
}
