import {render, screen, cleanup, fireEvent} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {CrmCard} from '../../surfaces/CrmCard';
afterEach(cleanup);
it('renders configured sections, formatted money and linked names without exposing unavailable fields',()=>{
 const read=vi.fn();
 render(<CrmCard busy={false} onRead={read} record={{id:'x',object_type:'Opportunity',revision:2,fields:{Name:'Test mandate',Amount:"150000000",CloseDate:'2027-01-29',AccountId:'raw-id',Description:'**Meeting evidence**'},narrative:'Shared **notes**',links:[{field:'AccountId',record_id:'a',label:'Test account'}],card:{version:1,layout:{title_field:'Name',show_related:false,sections:[{title:'Mandate',fields:[{field:'Amount',label:'Investment',format:'currency'},{field:'CloseDate',format:'date'},{field:'AccountId',label:'Account'},{field:'Secret',label:'Hidden'},{field:'Description',format:'markdown'}]}]}}}}/>);
 expect(screen.getByText('$150,000,000')).toBeTruthy();
 expect(screen.getByText('Jan 29, 2027')).toBeTruthy();
 expect(screen.queryByText('Hidden')).toBeNull();
 expect(screen.getByText('Meeting evidence').tagName).toBe('STRONG');
 fireEvent.click(screen.getByRole('button',{name:'Test account'}));expect(read).toHaveBeenCalledWith('a');
 expect(screen.getByText('All fields · 5').closest('details')?.open).toBe(false);
});
