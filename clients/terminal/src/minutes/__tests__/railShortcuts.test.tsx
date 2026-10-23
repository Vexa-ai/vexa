import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Rail } from '../Rail';
import { shortcutLabel, RAIL_SHORTCUTS, matchesShortcut } from '../../ui-kit/shortcuts';
vi.mock('../AccountBadge',()=>({AccountBadge:()=>null}));
vi.mock('../../surfaces/chatActivity',()=>({useChatActive:()=>false}));

// Vexa-ai/vexa#1829 follow-up — founder 2026-10-11: "add keyboard shortcuts" to the row menu.
// jsdom's navigator is not a Mac, so the primary modifier here is Ctrl.
// DELETE CONFIRMS IN A DIALOG (design guidelines §4.11, S7): the row's in-place ✓ these tests first
// armed was replaced by a ConfirmDialog whose button names the act, "Delete chat". The behaviour is
// the same — the key only opens the confirm, the chat is deleted by the confirm, never by the key.
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

  it('Ctrl+Backspace only opens the confirm; the chat is deleted by its "Delete chat", never by the key',()=>{
    const {del}=setup();
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog',{name:'Delete this chat?'})).toBeTruthy();
    const ok=screen.getByRole('button',{name:'Delete chat'});
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(ok);
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
  });

  it('a rail in a closed drawer (inert) takes no shortcuts',()=>{
    const pin=vi.fn();
    render(<div inert={true as any}><Rail rows={[chat]} hidden={0} all={true} onAll={()=>{}} selKey="c:a" onSelect={()=>{}} onNewChat={()=>{}} onDeleteChat={()=>{}} onPin={pin} onHide={()=>{}}/></div>);
    fireEvent.keyDown(window,ctrl('KeyP'));
    expect(pin).not.toHaveBeenCalled();
  });

  it('Ctrl+Shift+E starts a new chat, and the + names the shortcut',()=>{
    const {newChat}=setup({selKey:null});
    fireEvent.keyDown(window,ctrl('KeyE'));
    expect(newChat).toHaveBeenCalledOnce();
    expect(screen.getByRole('button',{name:'New chat'}).getAttribute('aria-keyshortcuts')).toBe('Control+Shift+E');
  });

  it('typing is never taken: plain letters and the editing key do nothing from a text field',()=>{
    const {pin,hide,del}=setup();
    const box=screen.getByRole('textbox',{name:'composer'});
    box.focus();
    for(const k of ['p','h','d','x']) fireEvent.keyDown(box,{key:k,code:`Key${k.toUpperCase()}`});
    fireEvent.keyDown(box,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(pin).not.toHaveBeenCalled(); expect(hide).not.toHaveBeenCalled(); expect(del).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
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
    expect(screen.queryByRole('dialog')).toBeNull();
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
    expect(screen.getByRole('dialog',{name:'Delete this chat?'})).toBeTruthy();
  });

  // The menu's look moved from inline styles into the design system (ui-kit/layout/layout.css): the
  // face is chosen by `data-mono`, so the check reads the attribute and the rule the browser applies.
  it('the Hide sub-line is the menu text face, not monospace',()=>{
    setup();
    fireEvent.click(screen.getByRole('button',{name:'Actions for Inbox'}));
    const sub=screen.getByText('Bring it back from Hidden');
    expect(sub.hasAttribute('data-mono')).toBe(false);
    expect(sub.className).toContain('vx-ctxmenu-detail');
    const css=readFileSync(join(__dirname,'..','..','ui-kit','layout','layout.css'),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
    const rule=(sel:string)=>[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[1].split(',').map(x=>x.trim()).includes(sel)).map(m=>m[2]).join(';');
    expect(rule('.vx-ctxmenu-detail')).toMatch(/font-family:\s*var\(--font-sans\)/);
    expect(rule('.vx-ctxmenu-detail[data-mono]')).toMatch(/font-family:\s*var\(--font-mono\)/);
  });

  // The in-place ✓ cancelled when focus moved elsewhere; the dialog that replaced it is modal, so
  // focus cannot leave it — Escape (or Cancel) is the way out. What must still hold is the Mac case:
  // Safari and Firefox do not focus a button on click, so the confirm blurs to NOTHING first, and
  // that must not take the confirm away before its click lands.
  it('the armed confirm survives a blur to nothing (a Mac click), and Escape cancels it',()=>{
    const {del}=setup();
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    const ok=screen.getByRole('button',{name:'Delete chat'});
    fireEvent.blur(ok,{relatedTarget:null});
    fireEvent.click(screen.getByRole('button',{name:'Delete chat'}));
    expect(del).toHaveBeenCalledExactlyOnceWith('a');
    fireEvent.keyDown(window,{key:'Backspace',code:'Backspace',ctrlKey:true});
    expect(screen.getByRole('dialog',{name:'Delete this chat?'})).toBeTruthy();
    fireEvent.keyDown(document,{key:'Escape'});
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(del).toHaveBeenCalledOnce();
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
