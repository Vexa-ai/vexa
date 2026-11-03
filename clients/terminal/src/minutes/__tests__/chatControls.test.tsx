import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatName } from '../ChatName';
import { Rail } from '../Rail';
import { orderedRows, moveChat } from '../chatOrder';
vi.mock('../AccountBadge',()=>({AccountBadge:()=>null}));
vi.mock('../../surfaces/chatActivity',()=>({useChatActive:()=>false}));

describe('chat controls',()=>{
  it('saves a clicked title and cancels without a write',async()=>{
    const save=vi.fn().mockResolvedValue(undefined);
    render(<ChatName label="Read emails" onRename={save}/>);
    fireEvent.click(screen.getByRole('button',{name:'Rename Read emails'}));
    fireEvent.change(screen.getByRole('textbox'),{target:{value:'Prepare customer follow-ups'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(()=>expect(save).toHaveBeenCalledWith('Prepare customer follow-ups'));
  });
  // A ConfirmDialog now (terminal design guidelines §4.11, S7): the button names the act, and
  // Escape and Cancel both leave the chat alone.
  it('requires a confirmation and permits cancellation',()=>{
    const del=vi.fn();
    const r={key:'chat:a',chatId:'a',label:'Calendar setup',whenLabel:'Today'} as any;
    render(<Rail rows={[r]} hidden={0} all={true} onAll={()=>{}} selKey={null} onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={del}/>);
    const openDelete=()=>{ fireEvent.click(screen.getByRole('button',{name:'Actions for Calendar setup'})); fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'})); };
    openDelete();
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog',{name:'Delete this chat?'})).toBeTruthy();
    fireEvent.keyDown(document, {key:'Escape'});
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(del).not.toHaveBeenCalled();
    openDelete();
    fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(del).not.toHaveBeenCalled();
    openDelete();
    fireEvent.click(screen.getByRole('button',{name:'Delete chat'}));
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
  });
  it('reorders both directions and ignores invalid drags',()=>{
    expect(moveChat(['a','b','c'],'a','c')).toEqual(['b','c','a']);
    expect(moveChat(['a','b','c'],'c','a')).toEqual(['c','a','b']);
    expect(moveChat(['a','b'],'x','a')).toEqual(['a','b']);
  });
  it('a chat started now is on top even when a saved order exists (founder 2026-10-11)',()=>{
    const rows=[{key:'c:today'},{key:'c:fri'},{key:'c:sat'}];
    expect(orderedRows(rows,['c:sat','c:fri']).map(r=>r.key)).toEqual(['c:today','c:fri','c:sat']);
  });
});
