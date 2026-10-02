import {render, screen, cleanup, fireEvent} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {CrmTable} from '../../surfaces/CrmTable';
afterEach(cleanup);
it('renders authorized rows through the document table renderer and opens record cards',async()=>{
 const read=vi.fn();
 render(<CrmTable busy={false} onRead={read} objectType="Opportunity" layout={{sections:[{title:'Deal',fields:[{field:'Amount',format:'currency'},{field:'Secret',label:'Hidden'}]}]}} records={[{id:'native-1',object_type:'Opportunity',revision:1,narrative:null,fields:{Name:'Alpha | {literal}',Amount:'1200000'}}]}/>);
 const table=await screen.findByRole('table');
 expect(table.textContent).toContain('$1,200,000');
 expect(table.textContent).not.toContain('Hidden');
 expect(screen.queryByText(/simplified rendering/)).toBeNull();
 fireEvent.click(screen.getByRole('link',{name:'Alpha | {literal}'}));
 expect(read).toHaveBeenCalledWith('native-1');
});
it('renders a useful empty result',async()=>{
 render(<CrmTable busy={false} onRead={()=>{}} objectType="Account" layout={null} records={[]}/>);
 expect(await screen.findByText('No records match this view.')).toBeTruthy();
});
