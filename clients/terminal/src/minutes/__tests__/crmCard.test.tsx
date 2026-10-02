import {render, screen, cleanup, fireEvent} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {CrmCard} from '../../surfaces/CrmCard';
afterEach(cleanup);
it('renders configured sections, formatted money and linked names without exposing unavailable fields',async()=>{
 const read=vi.fn();
 render(<CrmCard busy={false} onRead={read} record={{id:'x',object_type:'Opportunity',revision:2,fields:{Name:'Test mandate {literal}',Amount:"150000000",CloseDate:'2027-01-29',AccountId:'raw-id',Description:'**Meeting evidence**'},narrative:'Shared **notes**',links:[{field:'AccountId',record_id:'a',label:'Test account'}],card:{version:1,layout:{title_field:'Name',show_related:false,sections:[{title:'Mandate',fields:[{field:'Amount',label:'Investment',format:'currency'},{field:'CloseDate',format:'date'},{field:'AccountId',label:'Account'},{field:'Secret',label:'Hidden'},{field:'Description',format:'markdown'}]}]}}}}/>);
 await screen.findByText('Mandate');
 expect(screen.getByText('Test mandate {literal}',{selector:'div'})).toBeTruthy();
 expect(screen.queryByText(/simplified rendering/)).toBeNull();
 expect(screen.getByText('$150,000,000')).toBeTruthy();
 expect(screen.getByText('Jan 29, 2027')).toBeTruthy();
 expect(screen.queryByText('Hidden')).toBeNull();
 expect(screen.getByText('Meeting evidence').tagName).toBe('STRONG');
 fireEvent.click(screen.getByRole('link',{name:'Test account'}));expect(read).toHaveBeenCalledWith('a');
 expect(screen.getByText('All fields · 5').closest('details')?.open).toBe(false);
});
it('renders the native Markdown description and graph backlinks',async()=>{
 render(<CrmCard busy={false} onRead={()=>{}} record={{id:'x',object_type:'Account',revision:1,fields:{Name:'Graph account'},narrative:'A **Markdown** description.',links:[{field:'Referenced by',record_id:'source',label:'Source account'}]}}/>);
 await screen.findByText('Description');
 expect(screen.getByText('Markdown').tagName).toBe('STRONG');
 expect(screen.getByRole('link',{name:'Source account'}).getAttribute('href')).toBe('/crm?record=source');
});
it('puts narrative first, names people, omits empty fields and deduplicates connections',async()=>{
 const {cardDocument}=await import('../../surfaces/CrmCard');
 const doc=cardDocument({id:'p',object_type:'Contact',revision:1,fields:{FirstName:'Ada',LastName:'Example',Email:'',Title:'Partner'},narrative:'## Background\n\nA sourced profile.',links:[{field:'Related via AccountId',object_type:'Opportunity',record_id:'o',label:'Expansion'},{field:'Description',object_type:'Opportunity',record_id:'o',label:'Expansion'}]});
 expect(doc.startsWith('# Ada Example')).toBe(true);
 expect(doc.indexOf('A sourced profile')).toBeLessThan(doc.indexOf('## Overview'));
 expect(doc).not.toContain('**Email:**');
 expect(doc).toContain('### Opportunity');
 expect(doc.match(/\[Expansion\]/g)).toHaveLength(1);
});

it('keeps source description prose when native narrative creates Description graph edges',async()=>{
 const {cardDocument}=await import('../../surfaces/CrmCard');
 const doc=cardDocument({id:'a',object_type:'Account',revision:1,fields:{Name:'Account',Description:'Original source prose'},narrative:'Native body',links:[{field:'Description',record_id:'b',object_type:'Account',label:'Linked account'}]});
 expect(doc).toContain('Original source prose');
 expect(doc).toContain('[Linked account]');
});
