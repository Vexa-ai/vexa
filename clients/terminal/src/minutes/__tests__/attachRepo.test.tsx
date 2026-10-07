import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { AttachRepo } from "../AttachRepo";
import { ApiError } from "../../surfaces/apiClient";
import * as api from "../../surfaces/workspaceApi";
vi.mock("../../surfaces/workspaceApi", async (original) => ({
  ...(await original<typeof import("../../surfaces/workspaceApi")>()),
  readDeployKey: vi.fn(), ensureDeployKey: vi.fn(), importWorkspace: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.readDeployKey).mockResolvedValue({slug:"seed",public_key:null,fingerprint:null,add_as:"deploy key"});
});
afterEach(cleanup);
const setup = () => render(<AttachRepo onClose={() => {}} />);
const fill = (value = "https://github.com/acme/repo") => fireEvent.change(screen.getByLabelText("Repository"),{target:{value}});
describe("independent repository import", () => {
  it("imports a new workspace and announces its identity only on completion", async () => {
    const attached=vi.fn(); let complete!: (r: any) => void;
    vi.mocked(api.importWorkspace).mockImplementation((_r,_f,_t,progress)=>{
      progress("Cloning repository"); return new Promise(resolve=>{complete=resolve;});
    });
    render(<AttachRepo onClose={()=>{}} onAttached={attached}/>); fill();
    fireEvent.click(screen.getByText("Attach"));
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByText("Cloning repository")).toBeTruthy();
    expect(attached).not.toHaveBeenCalled();
    complete({workspace:"repo-123",cloned:true,changed:true,nested:false,repo:"https://github.com/acme/repo.git",ref:"main"});
    await screen.findByText("Cloned https://github.com/acme/repo.git into repo-123");
    expect(attached).toHaveBeenCalledWith("repo-123");
    expect(screen.getByText("Open workspace")).toBeTruthy();
  });
  it("uses SSH when the deploy key is selected", async () => {
    vi.mocked(api.ensureDeployKey).mockResolvedValue({slug:"seed",public_key:"ssh-ed25519 PUBLIC",fingerprint:"SHA256:public",add_as:"deploy key"});
    render(<AttachRepo embedded onClose={() => {}} />);fill();fireEvent.click(screen.getByText("Use this deploy key"));
    await waitFor(()=>expect((screen.getByLabelText("Repository") as HTMLInputElement).value).toBe("git@github.com:acme/repo.git"));
  });
  it("rejects credentials pasted into the repository field before sending",async()=>{
    setup();fill("ghp_secret123456789012345678901234567890123456");
    fireEvent.click(screen.getByText("Attach"));expect(api.importWorkspace).not.toHaveBeenCalled();
  });
  it("reports repository access errors instead of backend outages",async()=>{
    vi.mocked(api.importWorkspace).mockRejectedValue(new ApiError(502,"git clone failed: Permission denied (publickey)", "/api/workspace/import"));
    setup();fill();fireEvent.click(screen.getByText("Attach"));
    await screen.findByText("⚠ Repository access failed. Check the repository URL and credential.");
    expect(screen.queryByText("Open workspace")).toBeNull();
  });
});
