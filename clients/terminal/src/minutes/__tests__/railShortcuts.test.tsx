import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Rail } from '../Rail';
import { shortcutLabel, RAIL_SHORTCUTS, matchesShortcut } from '../../ui-kit/shortcuts';
vi.mock('../AccountBadge',()=>({AccountBadge:()=>null}));
vi.mock('../../surfaces/chatActivity',()=>({useChatActive:()=>false}));

// Vexa-ai/vexa#1829 follow-up — founder 2026-10-11: "add keyboard shortcuts" to the row menu.
// jsdom's navigator is not a Mac, so the primary modifier here is Ctrl.
const chat={key:'c:a',chatId:'a',label:'Inbox',whenLabel:'1:36 PM'} as any;
const meeting={key:'m:42',chatId:null,meetingId:'42',label:'Weekly',whenLabel:'Sat'} as any;
const ctrl=(code:string,extra:Record<string,unknown>={})=>({key:code.replace(/^Key/,''),code,ctrlKey:true,shiftKey:true,...extra});

function setup(over:Record<string,unknown>={}){
  const h={pin:vi.fn(),hide:vi.fn(),del:vi.fn(),newChat:vi.fn()};
  const props:any={rows:[chat,meeting],hidden:0,all:true,onAll:()=>{},selKey:'c:a',onSelect:()=>{},
    onNewChat:h.newChat,onDeleteChat:h.del,onPin:h.pin,onHide:h.hide,onHiddenView:()=>{},hiddenMarked:0,...over};
  const view=render(<><textarea aria-label="composer"/><Rail {...props}/></>);
  return {...h,view,props};
}

describe('rail keyboard shortcuts',()=>{
  afterEach(()=>{cleanup();vi.unstubAllGlobals();});

  it('Ctrl+Shift+P pins the selected chat, and unpins a pinned one',()=>{
    const {pin,view,props}=setup();
    fireEvent.keyDown(window,ctrl('KeyP'));
    expect(pin).toHaveBeenCalledExactlyOnceWith(chat,true);
    const pinned={...chat,pinned:true};
    view.rerender(<><textarea aria-label="composer"/><Rail {...props} rows={[pinned,meeting]}/></>);
    fireEvent.keyDown(window,ctrl('KeyP'));
    expect(pin).toHaveBeenLastCalledWith(pinned,false);
  });

  it('Ctrl+Shift+X hides the selected row; in the Hidden view it unhides',()=>{
    const {hide,view,props}=setup();
    fireEvent.keyDown(window,ctrl('KeyX'));
    expect(hide).toHaveBeenCalledExactlyOnceWith(chat,true);
    const hidden={...chat,hidden:true};
    view.rerender(<><textarea aria-label="composer"/><Rail {...props} rows={[hidden]} hiddenView/></>);
    fireEvent.keyDown(window,ctrl('KeyX'));
    expect(hide).toHaveBeenLastCalledWith(hidden,false);
  });

  it('Ctrl+Backspace only arms the confirm; the chat is deleted by the ✓, never by the key',()=>{
    const {del}=setup();
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(del).not.toHaveBeenCalled();
    const ok=screen.getByRole('button',{name:'Confirm delete Inbox'});
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(ok);
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
  });

  it('Ctrl+Shift+E starts a new chat',()=>{
    const {newChat}=setup({selKey:null});
    fireEvent.keyDown(window,ctrl('KeyE'));
    expect(newChat).toHaveBeenCalledOnce();
  });

  it('typing is never taken: plain letters and the editing key do nothing from a text field',()=>{
    const {pin,hide,del}=setup();
    const box=screen.getByRole('textbox',{name:'composer'});
    box.focus();
    for(const k of ['p','h','d','x']) fireEvent.keyDown(box,{key:k,code:`Key${k.toUpperCase()}`});
    fireEvent.keyDown(box,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(pin).not.toHaveBeenCalled(); expect(hide).not.toHaveBeenCalled(); expect(del).not.toHaveBeenCalled();
    expect(screen.queryByRole('button',{name:'Confirm delete Inbox'})).toBeNull();
    // a shortcut WITH the modifier still works from the composer
    fireEvent.keyDown(box,ctrl('KeyP'));
    expect(pin).toHaveBeenCalledOnce();
  });

  it('never acts on a row the person cannot see, or one whose menu does not offer the act',()=>{
    const {pin,hide,del}=setup({selKey:'c:gone'});      // selected chat filtered out of this list
    fireEvent.keyDown(window,ctrl('KeyP')); fireEvent.keyDown(window,ctrl('KeyX'));
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(pin).not.toHaveBeenCalled(); expect(hide).not.toHaveBeenCalled();
    cleanup();
    const m=setup({selKey:'m:42'});                       // a meeting with no chat has nothing to delete
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(screen.queryByRole('button',{name:/Confirm delete/})).toBeNull();
    expect(del).not.toHaveBeenCalled(); expect(m.del).not.toHaveBeenCalled();
  });

  it('nothing fires while a modal is open or the other modifier is held',()=>{
    const {pin}=setup();
    fireEvent.keyDown(window,ctrl('KeyP',{metaKey:true}));
    fireEvent.keyDown(window,ctrl('KeyP',{altKey:true}));
    fireEvent.keyDown(window,{key:'P',code:'KeyP',ctrlKey:true});         // no Shift: not the combo
    const modal=document.createElement('div'); modal.setAttribute('aria-modal','true'); document.body.appendChild(modal);
    fireEvent.keyDown(window,ctrl('KeyP'));
    modal.remove();
    expect(pin).not.toHaveBeenCalled();
  });

  it('the open menu shows each shortcut, names it for assistive tech, and takes P, H and D',()=>{
    const {pin,hide,del}=setup();
    const open=()=>fireEvent.click(screen.getByRole('button',{name:'Actions for Inbox'}));
    open();
    const pinItem=screen.getByRole('menuitem',{name:/Pin to top/});
    expect(pinItem.getAttribute('aria-keyshortcuts')).toBe('Control+Shift+P P');
    expect(pinItem.textContent).toContain('Ctrl+Shift+P');
    expect(screen.getByRole('menuitem',{name:/^Hide/}).getAttribute('aria-keyshortcuts')).toBe('Control+Shift+X H');
    expect(screen.getByRole('menuitem',{name:/Delete/}).getAttribute('aria-keyshortcuts')).toBe('Control+Backspace D');
    // while the menu is open the global combo is the menu's, not a second act
    fireEvent.keyDown(pinItem,{key:'p',code:'KeyP'});
    expect(pin).toHaveBeenCalledExactlyOnceWith(chat,true);
    expect(screen.queryByRole('menu')).toBeNull();
    open(); fireEvent.keyDown(screen.getByRole('menu'),{key:'h',code:'KeyH'});
    expect(hide).toHaveBeenCalledExactlyOnceWith(chat,true);
    open(); fireEvent.keyDown(screen.getByRole('menu'),{key:'d',code:'KeyD'});
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'Confirm delete Inbox'})).toBeTruthy();
  });

  it('the Hide sub-line is the menu text face, not monospace',()=>{
    setup();
    fireEvent.click(screen.getByRole('button',{name:'Actions for Inbox'}));
    const sub=screen.getByText('Bring it back from Hidden');
    expect(sub.style.fontFamily).toBe('var(--sans)');
  });

  it('the armed ✓ survives a blur to nothing (a Mac click) and cancels when focus moves elsewhere',()=>{
    const {del}=setup();
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    const ok=screen.getByRole('button',{name:'Confirm delete Inbox'});
    fireEvent.blur(ok,{relatedTarget:null});
    fireEvent.click(screen.getByRole('button',{name:'Confirm delete Inbox'}));
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    fireEvent.blur(screen.getByRole('button',{name:'Confirm delete Inbox'}),{relatedTarget:screen.getByRole('textbox',{name:'composer'})});
    expect(screen.queryByRole('button',{name:'Confirm delete Inbox'})).toBeNull();
  });

  it('on a Mac the modifier is ⌘ and the hint is Apple-ordered',()=>{
    expect(shortcutLabel(RAIL_SHORTCUTS.pin,true)).toBe('⇧⌘P');
    expect(shortcutLabel(RAIL_SHORTCUTS.delete,true)).toBe('⌘⌫');
    const ev=(o:Record<string,unknown>)=>new KeyboardEvent('keydown',{key:'P',code:'KeyP',shiftKey:true,...o});
    expect(matchesShortcut(ev({metaKey:true}),RAIL_SHORTCUTS.pin,true)).toBe(true);
    expect(matchesShortcut(ev({ctrlKey:true}),RAIL_SHORTCUTS.pin,true)).toBe(false);
    // the physical key, so a non-Latin layout presses what the hint says
    expect(matchesShortcut(new KeyboardEvent('keydown',{key:'З',code:'KeyP',shiftKey:true,ctrlKey:true}),RAIL_SHORTCUTS.pin,false)).toBe(true);
  });
});
