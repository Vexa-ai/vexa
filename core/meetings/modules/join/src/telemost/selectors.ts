// Yandex Telemost (web client) selectors — the JOIN layer's read surface
// (join / admission / leave / removal). Speaker-tile and capture selectors are
// recording concerns and stay OUTSIDE this brick.
//
// Telemost is a hosted SPA with no public runtime API on the page (no analogue
// of Jitsi's `APP.conference`), so every verdict here is read from the DOM and
// the page console. `data-testid` attributes are the stable handles (class
// names are hashed per deploy); text lists back them up in Russian and English,
// because the UI may localise from the account/region regardless of the pinned
// browser locale (BOT_UI_LOCALE).
//
// TEXT-SELECTOR SEMANTICS (Playwright): quoted `text="foo"` is EXACT match
// (case-sensitive); unquoted `text=foo` is SUBSTRING (case-insensitive).
// `*Texts` exports are raw strings scanned inside page.evaluate() — not
// Playwright selectors. src/shared/selector-validity.test.ts gates the
// `*Selectors` / `*Indicators` arrays; `browserContextSelectorArrays` names the
// arrays that also run through document.querySelector.

// ---- Hosts ----
// The public Telemost deployments: personal accounts on telemost.yandex.*,
// Yandex 360 for Business on telemost.360.yandex.*.
export const telemostHosts: string[] = [
  "telemost.yandex.ru",
  "telemost.yandex.com",
  "telemost.360.yandex.ru",
  "telemost.360.yandex.com",
];

// ---- The call frame ----
// The page is a messenger shell; the call itself — pre-join card, waiting room,
// call screen — renders in a same-origin iframe at `/private-join/<id>`. Every
// call-control selector below is looked up inside that frame; the shell (main
// frame) carries only the announcement modals, the "meeting not found" page and
// the sign-in invitation.
export const telemostCallFramePattern = /\/private-join\/\d+/;

// ---- Web vs app choice ----
// A meeting link may first offer the desktop app ("Подключение к звонку"); the
// bot always stays in the browser.
export const telemostContinueInBrowserSelectors: string[] = [
  '[data-testid="meeting-continue-in-browser-continue"]',
];
export const telemostContinueInBrowserTexts = [
  "продолжить в браузере",
  "continue in browser",
];

// ---- One-off announcement modals ----
// Product-update dialogs ("Большое обновление в Телемосте") and "Понятно"
// banners that cover the pre-join form; confirmed and moved past.
export const telemostDismissSelectors: string[] = [
  '[data-testid="telemost-3-onboarding-confirm"]',
  '[data-testid="telemost-3-onboarding-close"]',
];
export const telemostDismissTexts = [
  "понятно",
  "звучит отлично",
  "got it",
];

// ---- Sign-in affordances — NEVER clicked ----
// The global bar carries "Войти" (Yandex ID login); a guest bot that clicks it
// lands on passport.yandex.* instead of the meeting.
export const telemostLoginTestIds = [
  "orb-global-bar-login-button",
];
export const telemostLoginHosts = [
  "passport.yandex.ru",
  "passport.yandex.com",
];

// ---- Pre-join page ----

// Display-name input on the guest pre-join screen ("Ваше имя на встрече").
export const telemostNameInputSelector =
  'input[data-testid="orb-textinput-input"], input[name="name"], input[placeholder*="имя" i], input[placeholder*="name" i]';

// Pre-join camera / microphone toggles — the bot is receive-only, so both are
// switched off before joining. Absent when the browser exposes no such device.
export const telemostPrejoinMuteSelectors: string[] = [
  '[data-testid="turn-off-mic-button"]',
  '[data-testid="turn-off-camera-button"]',
  '[data-testid="turn-off-cam-button"]',
];

// The pre-join "Подключиться" button.
export const telemostJoinButtonSelectors: string[] = [
  '[data-testid="enter-conference-button"]',
];
export const telemostJoinButtonTexts = [
  "подключиться",
  "присоединиться",
  "join",
  "join meeting",
];

// Elements that only render on the pre-join screen (distinguish "still
// pre-join" from "in the call" in the admission oracle).
export const telemostPrejoinScreenSelectors: string[] = [
  'input[data-testid="orb-textinput-input"]',
  '[data-testid="enter-conference-button"]',
  '[data-testid="meeting-continue-in-browser-continue"]',
];

// ---- In-meeting admission indicators ----

// The leave control only renders inside the call — the primary DOM positive.
export const telemostLeaveButtonSelectors: string[] = [
  '[data-testid="end-call-alt-button"]',
  '[data-testid*="end-call" i]',
  'button[aria-label*="Покинуть" i]',
  'button[title*="Покинуть" i]',
  'button[aria-label*="Leave" i]',
];

// The shell's call bar ("Групповой звонок" mini-panel) carries its own hang-up —
// the leave fallback when the call frame's toolbar is unreachable.
export const telemostShellHangupSelectors: string[] = [
  'button[aria-label="Завершить звонок"]',
  'button[aria-label="End call"]',
];

// Controls that render only in the call toolbar. Never sufficient alone —
// the oracle pairs them with the absence of pre-join and waiting signals.
export const telemostConferenceIndicators: string[] = [
  '[data-testid="participants-button"]',
  '[data-testid="chat-alt-button"]',
  '[data-testid="hand-up-button"]',
  '[data-testid="more-popup-alt-button"]',
];

// ---- Waiting room ("комната ожидания") ----
export const telemostLobbyTexts = [
  "комната ожидания",
  "комнате ожидания",
  "комнату ожидания",
  "зал ожидания",
  "зале ожидания",
  "организатор впустит",
  "организатор скоро",
  "waiting room",
  "the organizer will let you in",
];

// ---- Terminal pre-admission pages ----
// A link to a meeting that does not exist, and the sign-in wall.
export const telemostNotFoundTexts = [
  "такого звонка нет",
  "такой встречи нет",
  "meeting not found",
];
// NOT "войдите в аккаунт": the sidebar shows that sign-in invitation to every guest on every
// page, joinable meetings included.
export const telemostNeedLoginTexts = [
  "войдите на яндекс",
  "войдите, чтобы подключиться",
];

// ---- Rejection / removal / end-of-meeting text (page.evaluate scans) ----
export const telemostRejectionTexts = [
  "организатор отклонил",
  "отклонил ваш запрос",
  "организатор не впустил",
  "запрос отклонён",
  "запрос отклонен",
  "request to join was declined",
];
export const telemostRemovalTexts = [
  "вы были удалены со встречи",
  "вас удалили из звонка",
  "вас удалили из встречи",
  "доступ к этому звонку закрыт",
  "организатор завершил встречу для всех",
  "звонок завершён",
  "звонок завершен",
  "you were removed",
  "the meeting has ended",
];

// The kicked-with-ban page.
export const telemostBannedPageIndicators: string[] = [
  '[data-test-tag="meeting-banned-page"]',
  '[data-testid="meeting-banned-go-home"]',
];

// ---- Media-engine disconnects (page console) ----
// The media engine logs its disconnect code; a kick or a closed room is
// terminal, and the log line lands even when the DOM shows nothing yet.
export const telemostTerminalConsolePattern =
  /\b(KICKED_OUT|ROOM_HAS_BEEN_CLOSED(?:_BY_TIMEOUT)?)\b/;

// ---- Post-meeting page ----
// After a leave/removal Telemost shows the rating dialog or its home screen.
export const telemostPostMeetingIndicators: string[] = [
  '[data-testid="telemost-feedback-modal"]',
  '[data-testid="create-call-button"]',
];
export const telemostPostMeetingTexts = [
  "оцените качество связи",
  "rate the call quality",
];

// Leave-confirmation dialog: a host leaving may be asked "leave or end for
// everyone" — the bot always picks plain leave.
export const telemostLeaveConfirmTexts = [
  "покинуть звонок",
  "покинуть встречу",
  "leave meeting",
];

/** The arrays this module ships into page.evaluate → document.querySelector. */
export const browserContextSelectorArrays: string[] = [
  "telemostLeaveButtonSelectors",
  "telemostPrejoinScreenSelectors",
  "telemostContinueInBrowserSelectors",
  "telemostDismissSelectors",
  "telemostJoinButtonSelectors",
  "telemostPrejoinMuteSelectors",
  "telemostShellHangupSelectors",
];
