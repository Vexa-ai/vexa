"use client";
/** "<call> is still running. Rejoin it?" — Rejoin / Start a new one (founder O1, 2026-10-18). The
 *  question `callRejoin.startOrAsk` raises when a call is about to start where one is already live. */
import { Button } from "../ui-kit";
import { Modal } from "../ui-kit/Modal";
import { closeRejoin, rejoinQuestion, useRejoinAsk } from "./callRejoin";
import "./shell2/shell2.css";

export function RejoinAsk() {
  const a = useRejoinAsk();
  if (!a) return null;
  return (
    <Modal title="A call is already running" onClose={closeRejoin}>
      <div className="vx-s2-rejoin" data-rejoin="" role="alertdialog" aria-label={rejoinQuestion(a.label)}>
        <p className="vx-s2-note">{rejoinQuestion(a.label)}</p>
        <div className="vx-s2-rejoin-acts">
          <Button variant="primary" data-rejoin-yes="" onClick={a.onRejoin}>Rejoin</Button>
          <Button variant="ghost" data-rejoin-new="" onClick={a.onNew}>Start a new one</Button>
        </div>
      </div>
    </Modal>
  );
}
