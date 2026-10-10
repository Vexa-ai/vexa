import { render, fireEvent, cleanup, screen } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { RecordingPlayer } from "../RecordingPlayer";
import { LiveTranscriptEngine } from "../../canvas/LiveTranscriptEngine";
import { segmentSeconds } from "../meetingPlayback";
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.getSelection()?.removeAllRanges(); });
const origin = 1791460000000;
const segments = [
  { speaker: "A", text: "First passage", tsMs: origin + 2000, endMs: origin + 4000 },
  { speaker: "A", text: "Second passage", tsMs: origin + 6000, endMs: origin + 9000 },
];
it("seeks and highlights individual passages within a speaker block, clearing in silence", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const { container, unmount } = render(<><RecordingPlayer meetingId="1" originMs={origin} src="/audio" onError={vi.fn()} /><LiveTranscriptEngine meetingId="1" segments={segments} /></>);
  const audio = container.querySelector("audio")!;
  fireEvent.click(screen.getByText("Second passage"));
  expect(audio.currentTime).toBe(6); expect(play).toHaveBeenCalledOnce();
  expect(screen.getByText("Second passage").getAttribute("data-playback-active")).toBe("true");
  audio.currentTime = 3; fireEvent.timeUpdate(audio);
  expect(screen.getByText("First passage").getAttribute("data-playback-active")).toBe("true");
  audio.currentTime = 5; fireEvent.timeUpdate(audio);
  expect(container.querySelector("[data-playback-active]")).toBeNull();
  fireEvent.keyDown(screen.getByText("Second passage"), { key: "Enter" }); expect(audio.currentTime).toBe(6);
  unmount(); render(<LiveTranscriptEngine meetingId="1" segments={segments} />);
  expect(screen.queryByRole("button")).toBeNull();
});
it("does not seek another meeting or hijack selection and links", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const { container, rerender } = render(<><RecordingPlayer meetingId="2" originMs={origin} src="/audio" onError={vi.fn()} /><LiveTranscriptEngine meetingId="1" segments={segments} /></>);
  expect(container.querySelector('[title^="Play from"]')).toBeNull();
  rerender(<><RecordingPlayer meetingId="1" originMs={origin} src="/audio" onError={vi.fn()} /><LiveTranscriptEngine meetingId="1" segments={segments} renderText={text => <a href="#entity">{text}</a>} /></>);
  fireEvent.click(screen.getByText("First passage")); expect(play).not.toHaveBeenCalled();
  const passage = screen.getByText("First passage").parentElement!;
  const range = document.createRange(); range.selectNodeContents(passage); window.getSelection()?.addRange(range);
  fireEvent.click(passage); expect(play).not.toHaveBeenCalled();
});
it("uses explicit clocks, never formatted wall times as audio offsets", () => {
  expect(segmentSeconds({ ts: "14:42:00" }, origin)).toBeNull();
  expect(segmentSeconds({ tsMs: origin })).toBeNull();
  expect(segmentSeconds({ tsMs: origin - 1000 }, origin)).toBeNull();
  expect(segmentSeconds({ ts: 2.75 })).toEqual({ start: 2.75, end: undefined });
  expect(segmentSeconds({ tsMs: origin + 2750, endMs: origin + 4100 }, origin)).toEqual({ start: 2.75, end: 4.1 });
});
