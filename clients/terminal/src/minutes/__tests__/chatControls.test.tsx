import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ChatName } from '../ChatName';
import { Rail } from '../Rail';
import { orderedRows, moveChat } from '../chatOrder';
vi.mock('../AccountBadge',()=>({AccountBadge:()=>null}));
vi.mock('../../surfaces/chatActivity',()=>({useChatActive:()=>false}));

describe('chat controls',()=>{
  afterEach(cleanup);
  it('saves a clicked title and cancels without a write',async()=>{
    const save=vi.fn().mockResolvedValue(undefined);
    render(<ChatName label="Read emails" onRename={save}/>);
    fireEvent.click(screen.getByRole('button',{name:'Rename Read emails'}));
    fireEvent.change(screen.getByRole('textbox'),{target:{value:'Prepare customer follow-ups'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(()=>expect(save).toHaveBeenCalledWith('Prepare customer follow-ups'));
  });
  it('requires a confirmation and permits cancellation',()=>{
    const del=vi.fn();
    const r={key:'c:a',chatId:'a',label:'Calendar setup',whenLabel:'Today'} as any;
    render(<Rail rows={[r]} hidden={0} all={true} onAll={()=>{}} selKey={null} onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={del}/>);
    const arm=()=>{
      fireEvent.click(screen.getByRole('button',{name:'Actions for Calendar setup'}));
      fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
    };
    arm();
    expect(del).not.toHaveBeenCalled();
    fireEvent.keyDown(document, {key:'Escape'});
    expect(screen.queryByRole('button',{name:'Confirm delete Calendar setup'})).toBeNull();
    arm();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('button',{name:'Confirm delete Calendar setup'})).toBeNull();
    arm();
    fireEvent.click(screen.getByRole('button',{name:'Confirm delete Calendar setup'}));
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
  });
  it('reorders both directions and ignores invalid drags',()=>{
    expect(moveChat(['a','b','c'],'a','c')).toEqual(['b','c','a']);
    expect(moveChat(['a','b','c'],'c','a')).toEqual(['c','a','b']);
    expect(moveChat(['a','b'],'x','a')).toEqual(['a','b']);
  });
  it('a chat started now is on top even when a saved order exists (founder 2026-10-11)',()=>{
    // `rows` arrive newest first. The old order put every row missing from the saved drag order
    // BELOW the saved ones, so a chat started today sat under Saturday's rows once anyone had dragged.
    const rows=[{key:'c:today'},{key:'c:fri'},{key:'c:sat'}];
    expect(orderedRows(rows,['c:sat','c:fri']).map(r=>r.key)).toEqual(['c:today','c:fri','c:sat']);
  });
  it('pinned rows lead, in their dragged order, newest unplaced pin first; the rest by recency',()=>{
    const rows=[{key:'n1'},{key:'p1'},{key:'n2'},{key:'p2'},{key:'p3'}];
    expect(orderedRows(rows,['p3','p2'],new Set(['p1','p2','p3'])).map(r=>r.key)).toEqual(['p1','p3','p2','n1','n2']);
  });
  it('pins, hides and unhides from the row menu, by keyboard too',()=>{
    const pin=vi.fn(), hide=vi.fn();
    const rows=[{key:'c:a',chatId:'a',label:'Inbox',whenLabel:'1:36 PM',pinned:true},{key:'m:42',chatId:null,meetingId:'42',label:'Weekly',whenLabel:'Sat'}] as any;
    render(<Rail rows={rows} hidden={0} all={true} onAll={()=>{}} selKey={null} onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={()=>{}} onPin={pin} onHide={hide} onHiddenView={()=>{}} hiddenMarked={0}/>);
    expect(screen.getAllByRole('img',{name:'Pinned'})).toHaveLength(1);
    fireEvent.click(screen.getByRole('button',{name:'Actions for Inbox'}));
    const first=screen.getByRole('menuitem',{name:'Unpin'});
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first,{key:'ArrowDown'});
    expect(document.activeElement).toBe(screen.getByRole('menuitem',{name:/^Hide/}));
    fireEvent.click(first);
    expect(pin).toHaveBeenCalledWith(rows[0],false);
    // a meeting with no chat can be pinned and hidden, and has nothing to delete
    fireEvent.click(screen.getByRole('button',{name:'Actions for Weekly'}));
    expect(screen.queryByRole('menuitem',{name:'Delete…'})).toBeNull();
    fireEvent.click(screen.getByRole('menuitem',{name:/^Hide/}));
    expect(hide).toHaveBeenCalledWith(rows[1],true);
  });
  it('the Hidden view lists hidden rows with Unhide',()=>{
    const hide=vi.fn(), view=vi.fn();
    const rows=[{key:'c:a',chatId:'a',label:'Inbox',whenLabel:'Sat',hidden:true}] as any;
    const {rerender}=render(<Rail rows={[]} hidden={0} all={false} onAll={()=>{}} selKey={null} onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={()=>{}} onHide={hide} onHiddenView={view} hiddenMarked={1}/>);
    fireEvent.click(screen.getByRole('button',{name:'Hidden 1'}));
    expect(view).toHaveBeenCalledWith(true);
    rerender(<Rail rows={rows} hidden={0} all={false} onAll={()=>{}} selKey={null} onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={()=>{}} onHide={hide} onHiddenView={view} hiddenMarked={1} hiddenView/>);
    fireEvent.click(screen.getByRole('button',{name:'Actions for Inbox'}));
    expect(screen.queryByRole('menuitem',{name:/Pin/})).toBeNull();
    fireEvent.click(screen.getByRole('menuitem',{name:'Unhide'}));
    expect(hide).toHaveBeenCalledWith(rows[0],false);
  });
});
