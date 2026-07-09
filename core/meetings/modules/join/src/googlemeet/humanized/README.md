# join/src/googlemeet/humanized — humanized X11 input

Human-like pointer/keyboard motion for Google Meet join (evades naive automation
detection). `humanizedInteraction.ts` (the interactor), `mocapEngine.ts` + `mocap-data.ts`
(recorded motion library → trajectories), `x11Input.ts` (xdotool/X11 driver), `types.ts`,
`index.ts`. Pure logic + X11 calls; the *environment* (Xvfb/xdotool) is provided by the
container, not this brick. `humanized.test.ts` is the L2 check.
