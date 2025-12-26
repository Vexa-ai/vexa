import { callStatusChangeCallback } from "./services/unified-callback";

export function log(message: string): void {
  console.log(`[BotCore] ${message}`);
}

export function randomDelay(amount: number) {
  return (2 * Math.random() - 1) * (amount / 10) + amount;
}

export async function callStartupCallback(botConfig: any): Promise<void> {
  await callStatusChangeCallback(botConfig, "active");
}

export async function callJoiningCallback(botConfig: any): Promise<void> {await callStatusChangeCallback(botConfig, "joining");}

export async function callAwaitingAdmissionCallback(botConfig: any): Promise<void> {
  await callStatusChangeCallback(botConfig, "awaiting_admission");
}

export async function callLeaveCallback(botConfig: any, reason: string = "manual_leave"): Promise<void> {
  // Note: Leave callback is typically handled by the exit callback with completion status
  // This function is kept for backward compatibility but may not be used
  log(`Leave callback requested with reason: ${reason} - handled by exit callback`);
}

