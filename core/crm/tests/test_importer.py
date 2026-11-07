from crm.importer import unresolved_records

def test_unresolved_links_and_unsupported_share_principals_are_quarantined():
    definitions={'Contact':{'properties':{'AccountId':{'referenceTo':['Account']}}},'Account':{'properties':{}}}
    rows={'Account':[{'Id':'a'}],'Contact':[{'Id':'c','AccountId':'missing'}],
          'AccountShare':[{'AccountId':'a','UserOrGroupId':'unknown'}]}
    problems=unresolved_records(rows,definitions)
    assert problems[('Contact','c')]==['Unresolved reference: AccountId']
    assert problems[('Account','a')]==['Unsupported share principal']
