import { render,screen,fireEvent,waitFor,cleanup } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { GitConnection } from '../GitConnection';
import { getGitToken,setGitToken } from '../../surfaces/workspaceApi';
vi.mock('../../surfaces/workspaceApi',()=>({getGitToken:vi.fn(),setGitToken:vi.fn(),readDeployKey:vi.fn().mockResolvedValue({})}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
it('submits directly to secure API and clears input without displaying the value',async()=>{
 vi.mocked(getGitToken).mockResolvedValue({set:false,masked:null});
 vi.mocked(setGitToken).mockResolvedValue({set:true,masked:null});
 render(<GitConnection/>);
 await screen.findByText('No saved token');
 const field=screen.getByLabelText('GitHub token') as HTMLInputElement;
 expect(field.type).toBe('password');
 fireEvent.change(field,{target:{value:'fixture-only-secret'}});
 fireEvent.click(screen.getByRole('button',{name:'Save securely'}));
 await screen.findByText('Token saved securely');
 expect(setGitToken).toHaveBeenCalledWith('fixture-only-secret');
 expect(field.value).toBe('');
 expect(screen.queryByText('fixture-only-secret')).toBeNull();
});

it('repository import stays inside the connection surface',async()=>{
 vi.mocked(getGitToken).mockResolvedValue({set:false,masked:null});
 const api=await import('../../surfaces/workspaceApi');
 vi.mocked(api.readDeployKey).mockResolvedValue({} as never);
 render(<GitConnection/>);
 fireEvent.click(screen.getByRole('button',{name:'Attach repository as a workspace'}));
 expect(screen.getByRole('region',{name:'Load an existing repository'})).toBeTruthy();
 expect(screen.queryByRole('dialog')).toBeNull();
 await waitFor(()=>expect(api.readDeployKey).toHaveBeenCalled());
});
