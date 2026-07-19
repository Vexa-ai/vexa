// Yandex Telemost web selectors. Attribute selectors are preferred because visible
// labels are localized. Text arrays are scanned case-insensitively in page.evaluate.

export const telemostBrowserContinueSelectors: string[] = [
  '[data-testid*="browser" i]',
  'button[aria-label*="browser" i]',
  'a[href*="telemost.yandex.ru/j/"]',
];

export const telemostNameInputSelectors: string[] = [
  'input[name="displayName"]',
  'input[name="name"]',
  'input[placeholder*="имя" i]',
  'input[placeholder*="name" i]',
];

export const telemostJoinButtonSelectors: string[] = [
  'button[data-testid*="join" i]',
  'button[aria-label*="подключ" i]',
  'button[aria-label*="продолж" i]',
  'button[aria-label*="join" i]',
  'button[aria-label*="continue" i]',
];

// These labels describe the action, therefore their presence means the device is
// currently enabled and must be clicked to make the receive-only bot silent.
export const telemostMuteActionSelectors: string[] = [
  'button[aria-label*="выключить микрофон" i]',
  'button[aria-label*="mute microphone" i]',
  'button[aria-label*="выключить камеру" i]',
  'button[aria-label*="turn off camera" i]',
];

export const telemostInMeetingIndicators: string[] = [
  'button[aria-label*="покинуть" i]',
  'button[aria-label*="завершить звонок" i]',
  'button[aria-label*="leave" i]',
  'button[aria-label*="hang up" i]',
  '[data-testid*="hangup" i]',
  '[data-testid*="participants" i]',
];

export const telemostPrejoinIndicators: string[] = [
  'input[name="displayName"]',
  'input[name="name"]',
  'input[placeholder*="имя" i]',
  'input[placeholder*="name" i]',
];

export const telemostWaitingTexts = [
  "ожидайте разрешения организатора",
  "ожидайте, пока организатор",
  "запрос на подключение отправлен",
  "waiting for the host",
  "waiting for approval",
];

export const telemostRejectionTexts = [
  "организатор отклонил запрос",
  "вам отказано в подключении",
  "request was declined",
  "request was rejected",
];

export const telemostRemovalTexts = [
  "вас удалили из видеовстречи",
  "встреча завершена",
  "звонок завершен",
  "you were removed from the meeting",
  "meeting has ended",
  "call has ended",
];

export const telemostBrowserContinueTexts = [
  "продолжить в браузере",
  "continue in browser",
];

export const telemostJoinTexts = [
  "продолжить",
  "подключиться",
  "join",
  "continue",
];
