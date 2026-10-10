import { render, fireEvent, screen, cleanup } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { RecordingPlayer, playbackTime } from "../RecordingPlayer";
afterEach(cleanup);
it("shows API total for WebM reporting Infinity and seeks against that total", () => {
  const { container } = render(<RecordingPlayer src="/audio" duration={1852} onError={vi.fn()} />);
  const audio = container.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { value: Infinity, configurable: true });
  fireEvent.loadedMetadata(audio);
  expect(screen.getByLabelText("Playback time").textContent).toBe("0:00 / 30:52");
  audio.currentTime = 15;
  fireEvent.timeUpdate(audio);
  expect(screen.getByLabelText("Playback time").textContent).toBe("0:15 / 30:52");
  fireEvent.change(screen.getByLabelText("Seek recording"), { target: { value: "900" } });
  expect(audio.currentTime).toBe(900);
});
it("prefers finite media duration and never invents a total", () => {
  const { container } = render(<RecordingPlayer src="/audio" onError={vi.fn()} />);
  expect(screen.getByLabelText("Playback time").textContent).toContain("Duration unknown");
  expect((screen.getByLabelText("Seek recording") as HTMLInputElement).disabled).toBe(true);
  const audio = container.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { value: 61 });
  fireEvent.durationChange(audio);
  expect(screen.getByLabelText("Playback time").textContent).toBe("0:00 / 1:01");
  expect(playbackTime(3661)).toBe("1:01:01");
});
