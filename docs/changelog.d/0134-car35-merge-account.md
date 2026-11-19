- **A CRM merge no longer moves an account (A2-c).** `crm_change`'s `merge` refuses, with a 409, to
  merge away a record someone signs in with (an attached account or a linked one). A CRM writer can no
  longer merge their own Person into someone else's record and then read that person's private
  profile, appear as them in the people directory, or join their accounts through a merge card.
  Duplicates still merge into the record the account answers to, and a person still joins their own
  accounts on their merge card. See [CRM](/minutes/crm).
