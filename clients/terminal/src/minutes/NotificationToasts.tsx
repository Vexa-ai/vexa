"use client";
/** THE TOASTS (#1783): a notification that arrives while the person is here shows top-right for six
 *  seconds — at most three stacked, newest on top. Its first act is one tap away (Accept, Open
 *  transcript, Open chat…); a click on the text opens what it is about. Calm, not red, and announced
 *  politely to a screen reader. On a phone it spans the width inside the gutter. */
import { X } from "lucide-react";
import { dismissToast, openNotice } from "./notifications";
import { useNotifications } from "./NotificationBell";
import { NoticeActs } from "./NoticeActs";
import { useFeature } from "../platform/features";
import { readableSentence } from "./callsList";

/** Hidden while `notifications` is switched off (`platform/features`). */
export function NotificationToasts() {
  return useFeature("notifications") ? <ToastStack /> : null;
}

function ToastStack() {
  const { toasts } = useNotifications();
  return (
    <div className="vx-toasts" role="status" aria-live="polite" data-toasts={toasts.length || undefined}>
      {toasts.map((n) => (
        <div key={n.id} className="vx-notice-toast" data-toast={n.kind}>
          <div className="vx-notice-toast-top">
            <button type="button" className="vx-toast-main" onClick={() => openNotice(n)}>
              <span aria-hidden className="vx-notice-dot" />
              <span className="vx-toast-text">{readableSentence(n.text)}</span>
            </button>
            <button type="button" className="vx-toast-close" aria-label="Dismiss" onClick={() => dismissToast(n.id)}>
              <X size={14} strokeWidth={1.75} aria-hidden />
            </button>
          </div>
          <NoticeActs n={n} />
        </div>
      ))}
    </div>
  );
}
